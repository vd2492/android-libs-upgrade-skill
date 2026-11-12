"""Tests for the project-detection parts that make the skill work on any Gradle project.

Run from anywhere:  python3 -m unittest discover -s scripts/tests
Each test builds a throwaway project in a temp dir and changes into it, since the scripts read the
repo they are run in.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _catalog  # noqa: E402
import modules  # noqa: E402


class InProject(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.old)

    def write(self, path, text):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)


class KeyResolutionTest(unittest.TestCase):
    def test_names_match_ignoring_case_and_separators(self):
        keys = ["agp", "androidGradlePlugin", "kotlin-version", "compile_sdk", "compileSdkVersion", "okhttp"]
        got = _catalog.resolve_keys(keys)
        self.assertEqual(got["agp"], "agp")
        self.assertEqual(got["androidGradlePlugin"], "agp")
        self.assertEqual(got["kotlin-version"], "kotlin")
        self.assertEqual(got["compile_sdk"], "compileSdk")
        self.assertEqual(got["compileSdkVersion"], "compileSdk")
        self.assertNotIn("okhttp", got)

    def test_an_override_wins_for_unusual_names(self):
        got = _catalog.resolve_keys(["weirdKey", "agp"], {"kotlin": "weirdKey"})
        self.assertEqual(got["weirdKey"], "kotlin")

    def test_platform_values_are_read_off_plugin_ids_when_no_key_matches(self):
        cat = _catalog.parse(
            '[versions]\nplug = "8.5.0"\nlang = "2.0.0"\n'
            '[plugins]\n'
            'app = { id = "com.android.application", version.ref = "plug" }\n'
            'kt = { id = "org.jetbrains.kotlin.android", version.ref = "lang" }\n')
        cat.rev = "x"   # skip the build-file lookup
        tc = cat.toolchain()
        self.assertEqual((tc["agp"], tc["kotlin"]), ("8.5.0", "2.0.0"))


class CatalogDiscoveryTest(InProject):
    def test_standard_location(self):
        self.write("gradle/libs.versions.toml", "[versions]\n")
        self.assertEqual(_catalog.catalog_path(), "gradle/libs.versions.toml")

    def test_any_versions_toml_under_gradle(self):
        self.write("gradle/deps.versions.toml", "[versions]\n")
        self.assertEqual(_catalog.catalog_path(), "gradle/deps.versions.toml")

    def test_config_overrides(self):
        self.write("gradle/libs.versions.toml", "[versions]\n")
        self.write("lib-upgrade.config.json", json.dumps({"catalog": "x/y.toml"}))
        self.assertEqual(_catalog.catalog_path(), "x/y.toml")

    def test_no_catalog_says_so_instead_of_guessing(self):
        with self.assertRaises(SystemExit) as cm:
            _catalog.catalog_path()
        self.assertIn("no Gradle version catalog", str(cm.exception))

    def test_sdk_levels_from_kotlin_and_groovy_build_files(self):
        self.write("app/build.gradle.kts", "android { compileSdk = 35\n defaultConfig { minSdk = 26\n targetSdk = 34 } }")
        self.assertEqual(_catalog.sdk_from_build_files(),
                         {"compileSdk": "35", "targetSdk": "34", "minSdk": "26"})
        self.write("app/build.gradle.kts", "")
        self.write("app/build.gradle", "android { compileSdkVersion 33\n minSdkVersion 21 }")
        self.assertEqual(_catalog.sdk_from_build_files()["compileSdk"], "33")


class ModulesTest(InProject):
    def test_include_forms_and_no_false_positives(self):
        self.write("settings.gradle.kts",
                   '// include(":commented")\n'
                   'pluginManagement { includeBuild("build-logic") }\n'
                   'include(":a", ":b:c")\n'
                   "include ':d'\n"
                   'maven { content { includeGroup("co.example") } }\n')
        for d in ("a", "b/c", "d"):
            self.write(f"{d}/build.gradle.kts", 'plugins { id("com.android.library") }')
        paths = [p["path"] for p in modules.projects() if p["path"] != ":"]
        self.assertEqual(sorted(paths), [":a", ":b:c", ":d"])

    def test_unit_test_task_follows_the_configured_variant(self):
        self.write("settings.gradle", "include ':lib'")
        self.write("lib/build.gradle", "plugins { id 'com.android.library' }")
        self.assertEqual(modules.projects()[0]["task"], ":lib:testDebugUnitTest")
        self.write("lib-upgrade.config.json", json.dumps({"unitTestVariant": "Staging"}))
        self.assertEqual(modules.projects()[0]["task"], ":lib:testStagingUnitTest")


if __name__ == "__main__":
    unittest.main()
