"""Regression tests for the review findings: fail-closed gates, offline handling, input validation.

Run from anywhere:  python3 -m unittest discover -s scripts/tests
Network is never touched: _maven.fetch is replaced with a fake.
"""
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import _catalog  # noqa: E402
import _maven  # noqa: E402
import modules  # noqa: E402
import screen_gates  # noqa: E402


class FakeRepo:
    """Replaces _maven.fetch: url suffix -> body. Unlisted URLs are definitive misses."""

    def __init__(self, files):
        self.files, self.calls = files, 0

    def __call__(self, url, timeout=40):
        self.calls += 1
        for suffix, body in self.files.items():
            if url.endswith(suffix):
                return body
        return None


class VersionOrderingTest(unittest.TestCase):
    def test_trailing_zeros_are_not_newer(self):
        self.assertEqual(_maven.key("1.0"), _maven.key("1.0.0"))
        self.assertEqual(_maven.stable_candidates(["1.0.0", "1.0.1"], "1.0"), ["1.0.1"])

    def test_tier_still_sees_minor_bump_from_short_version(self):
        self.assertEqual(_maven.tier("1.0", "1.1"), "T2")
        self.assertEqual(_maven.tier("1.0.0", "1.0.1"), "T1")
        self.assertEqual(_maven.tier("0.3.0", "0.4.0"), "T3")

    def test_prerelease_markers_are_whole_words(self):
        for stable in ("2.0.0-src", "1.0.0-device", "3.1.0-arch", "1.0.0-android", "33.7.1-jre"):
            self.assertTrue(_maven.is_stable(stable), stable)
        for unstable in ("1.0.0-RC1", "1.0.0-rc.1", "1.0.0-beta-2", "2.0.0-alpha01", "1.0.0-M3",
                         "1.0.0-dev-1234", "2.0.0-SNAPSHOT", "1.0.0-eap", "1.0.0rc1"):
            self.assertFalse(_maven.is_stable(unstable), unstable)

    def test_kotlin_line_ignores_trailing_zero_patch(self):
        self.assertEqual(screen_gates.line("2.0.0"), screen_gates.line("2.0.20"))
        self.assertLess(screen_gates.line("2.0.20"), screen_gates.line("2.1.0"))


class OfflineTest(unittest.TestCase):
    def setUp(self):
        _maven.clear_cache()

    def test_transient_failure_is_not_cached_and_is_unreachable(self):
        boom = urllib.error.URLError("down")
        with mock.patch("urllib.request.urlopen", side_effect=boom), mock.patch("time.sleep"):
            with self.assertRaises(_maven.Unreachable):
                _maven.versions("androidx.core", "core-ktx")
            self.assertTrue(_maven.failed("https://dl.google.com/dl/android/maven2/androidx/core/core-ktx/maven-metadata.xml"))
        # a later call retries instead of replaying the cached failure
        meta = b"<metadata><versions><version>1.0.0</version></versions></metadata>"
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = meta
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(_maven.versions("androidx.core", "core-ktx")[1], ["1.0.0"])

    def test_404_is_definitive_and_cached(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err) as m:
            self.assertIsNone(_maven.fetch("https://example.invalid/x"))
            self.assertIsNone(_maven.fetch("https://example.invalid/x"))
            self.assertEqual(m.call_count, 1)
        self.assertFalse(_maven.failed("https://example.invalid/x"))

    def test_genuinely_unpublished_is_empty_not_unreachable(self):
        with mock.patch.object(_maven, "fetch", FakeRepo({})):
            _maven.versions.cache_clear()
            self.assertEqual(_maven.versions("no.such", "lib"), (None, []))

    def test_screen_ref_reports_unknown_not_diverged_when_offline(self):
        cat = _catalog.parse('[versions]\ncoil="3.0.0"\n[libraries]\n'
                             'coil={module="io.coil-kt.coil3:coil",version.ref="coil"}\n')
        with mock.patch.object(_maven, "versions", side_effect=_maven.Unreachable("offline")):
            r = screen_gates.screen_ref_at(cat, "coil", "3.1.0", 35, "2.3.20")
        self.assertEqual(r["verdict"], "UNKNOWN")

    def test_published_or_exit_never_says_up_to_date_offline(self):
        art = _catalog.Artifact("coil", "io.coil-kt.coil3", "coil", "coil", None)
        with mock.patch.object(_maven, "versions", side_effect=_maven.Unreachable("offline")):
            with self.assertRaises(SystemExit) as cm:
                screen_gates.published_or_exit(art)
        self.assertIn("UNKNOWN", str(cm.exception))


class StdlibGateTest(unittest.TestCase):
    def gate2(self, pom):
        _maven.clear_cache()
        with mock.patch.object(_maven, "fetch", FakeRepo({"/lib-1.0.pom": pom.encode()})):
            return screen_gates.screen_coord("g", "lib", "1.0", 35, "2.3.20")["gate2"]

    def dep(self, version="", scope=""):
        v = f"<version>{version}</version>" if version else ""
        s = f"<scope>{scope}</scope>" if scope else ""
        return (f"<project><dependencies><dependency><groupId>org.jetbrains.kotlin</groupId>"
                f"<artifactId>kotlin-stdlib</artifactId>{v}{s}</dependency></dependencies></project>")

    def test_newer_stdlib_fails(self):
        self.assertEqual(self.gate2(self.dep("2.4.0"))[0], "FAIL")

    def test_same_line_passes(self):
        self.assertEqual(self.gate2(self.dep("2.3.21"))[0], "PASS")

    def test_unversioned_stdlib_is_unknown_not_pass(self):
        self.assertEqual(self.gate2(self.dep())[0], "UNKNOWN")

    def test_unresolved_property_is_unknown_not_pass(self):
        self.assertEqual(self.gate2(self.dep("${kotlin.version}"))[0], "UNKNOWN")

    def test_property_resolves_from_pom(self):
        pom = ("<project><properties><kotlin.version>2.4.0</kotlin.version></properties><dependencies><dependency>"
               "<groupId>org.jetbrains.kotlin</groupId><artifactId>kotlin-stdlib</artifactId>"
               "<version>${kotlin.version}</version></dependency></dependencies></project>")
        self.assertEqual(self.gate2(pom)[0], "FAIL")

    def test_test_scope_stdlib_is_ignored(self):
        self.assertEqual(self.gate2(self.dep("2.4.0", "test"))[0], "PASS")

    def test_range_is_unknown(self):
        self.assertEqual(self.gate2(self.dep("[1.9,2.0)"))[0], "UNKNOWN")


class AccessorTest(unittest.TestCase):
    def match(self, accessor_re, text):
        import re
        return bool(re.search(accessor_re + modules.ACCESSOR_END, text))

    def test_provider_calls_still_count_as_use(self):
        for text in ("libs.versions.compose.get()", "libs.versions.compose.get().toInt()",
                     "implementation(libs.retrofit)", "x = libs.versions.compose.asProvider()"):
            self.assertTrue(self.match(r"libs\.versions\.compose|libs\.retrofit", text), text)

    def test_longer_alias_is_not_a_match(self):
        self.assertFalse(self.match(r"libs\.foo", "libs.foo.bar"))
        self.assertFalse(self.match(r"libs\.foo", "libs.foo-extra"))
        self.assertFalse(self.match(r"libs\.foo", "libs.fooBar"))


class InProject(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.dir = os.path.realpath(tempfile.mkdtemp())
        os.chdir(self.dir)
        subprocess.run(["git", "init", "-q", "."], check=True)

    def tearDown(self):
        os.chdir(self.old)

    def write(self, path, text):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)


class SettingsParsingTest(InProject):
    def test_multiline_groovy_include_without_parens(self):
        self.write("settings.gradle", "include ':a',\n        ':b',\n        ':c'\nrootProject.name = 'x'\n")
        for m in "abc":
            self.write(f"{m}/build.gradle", "")
        self.assertEqual({p["path"] for p in modules.projects()} - {":"}, {":a", ":b", ":c"})

    def test_kmp_test_source_sets_count_as_tests(self):
        self.write("settings.gradle.kts", 'include(":shared", ":plain")\n')
        self.write("shared/build.gradle.kts", "")
        self.write("shared/src/commonTest/kotlin/T.kt", "class T")
        self.write("plain/build.gradle.kts", "")
        self.write("plain/src/main/kotlin/M.kt", "class M")
        got = {p["path"]: p["has_tests"] for p in modules.projects()}
        self.assertTrue(got[":shared"])
        self.assertFalse(got[":plain"])

    def test_sdk_values_are_read_in_a_stable_order(self):
        self.write("app/build.gradle", "android { minSdk = 26 }")
        self.write("lib/build.gradle", "android { minSdk = 21 }")
        for _ in range(3):
            self.assertEqual(_catalog.sdk_from_build_files()["minSdk"], "26")


class InputValidationTest(InProject):
    def test_option_like_revision_is_refused(self):
        for rev in ("--output=/tmp/x", "-p"):
            with self.assertRaises(SystemExit):
                _catalog.read_file_at("gradle/libs.versions.toml", rev)
            with self.assertRaises(SystemExit):
                _catalog.check_rev(rev)
        _catalog.check_rev("HEAD~1")
        _catalog.check_rev(None)

    def test_run_names_cannot_escape(self):
        for bad in ("../x", "a/b", ".hidden", "", "a b"):
            with self.assertRaises(SystemExit):
                _catalog.safe_name(bad)
        self.assertEqual(_catalog.safe_name("coil-3.5.0"), "coil-3.5.0")

    def run_gradle(self, *args):
        script = os.path.join(HERE, "..", "run_gradle.sh")
        return subprocess.run(["bash", script, *args], capture_output=True, text=True, cwd=self.dir)

    def test_run_gradle_refuses_outside_git_repo(self):
        with tempfile.TemporaryDirectory() as plain:
            script = os.path.join(HERE, "..", "run_gradle.sh")
            r = subprocess.run(["bash", script, "t", "tasks"], capture_output=True, text=True, cwd=plain)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not inside a git repository", r.stderr)

    def test_run_gradle_refuses_missing_wrapper_and_bad_name(self):
        self.assertEqual(self.run_gradle("t", "tasks").returncode, 2)
        self.assertEqual(self.run_gradle("../evil", "tasks").returncode, 2)

    def test_run_gradle_records_real_exit_code(self):
        self.write("gradlew", "#!/bin/sh\nexit 7\n")
        os.chmod(os.path.join(self.dir, "gradlew"), 0o755)
        r = self.run_gradle("t", "tasks")
        self.assertEqual(r.returncode, 7)
        with open(os.path.join(self.dir, "build/lib-upgrade/runs/t.rc")) as fh:
            self.assertEqual(fh.read().strip(), "7")


try:
    import openpyxl  # noqa: F401
    import tracker
except (ImportError, SystemExit):  # pragma: no cover
    tracker = None


@unittest.skipIf(tracker is None, "openpyxl not installed")
class TrackerTest(InProject):
    def test_formula_text_stays_text_and_save_is_atomic(self):
        path = os.path.join(self.dir, "t.xlsx")
        wb = tracker.open_book(path)
        ws = tracker.sheet(wb, "libs", create=True)
        hdr = tracker.headers(ws)
        tracker.write(ws, hdr, 2, {"lib name": "a", "status": "pending", "code changes": '=HYPERLINK("http://e","x")'})
        tracker.save_book(wb, path)
        self.assertEqual([f for f in os.listdir(self.dir) if "tmp" in f], [])
        cell = ws = openpyxl.load_workbook(path)["Lib Upgrades"].cell(row=2, column=hdr["code changes"])
        self.assertEqual(cell.data_type, "s")


if __name__ == "__main__":
    unittest.main()
