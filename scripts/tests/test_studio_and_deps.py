"""Unit tests for studio.py and screen_gates.dependencies_of - run from the repo root:

    python3 -m unittest discover -s .claude/skills/lib-upgrade/scripts/tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import screen_gates  # noqa: E402
import studio  # noqa: E402


class SupportTest(unittest.TestCase):
    def test_exact_version_is_sure(self):
        self.assertEqual(studio.support((2026, 2, 1)), {"status": "OK", "sure": (9, 4), "possible": (9, 4)})
        self.assertEqual(studio.support((2026, 1, 1))["sure"], (9, 2))

    def test_minor_only_is_a_range_when_the_patch_changes_the_answer(self):
        s = studio.support((2026, 2))
        self.assertEqual((s["status"], s["sure"], s["possible"]), ("PATCH", (9, 4), (9, 5)))

    def test_unknown_versions(self):
        self.assertEqual(studio.support((2027, 1))["status"], "UNKNOWN")
        self.assertEqual(studio.support((2026, 2, 9))["status"], "UNKNOWN")


class CheckAgpTest(unittest.TestCase):
    def code(self, studio_v, agp):
        return studio.check_agp(studio_v, agp)[0]

    def test_supported(self):
        self.assertEqual(self.code((2026, 2, 2), "9.5.1"), 0)
        self.assertEqual(self.code((2026, 2, 1), "9.4.1"), 0)

    def test_needs_newer_studio(self):
        code, msg = studio.check_agp((2026, 2, 1), "9.5.1")
        self.assertEqual(code, 1)
        self.assertIn("2026.2.2", msg)
        self.assertEqual(self.code((2026, 1, 1), "9.4.0"), 1)

    def test_depends_on_patch_or_unknown(self):
        self.assertEqual(self.code((2026, 2), "9.5.1"), 2)
        self.assertEqual(self.code((2027, 1), "9.5.1"), 2)

    def test_agp_beyond_the_table_does_not_claim_support(self):
        code, msg = studio.check_agp((2026, 2, 2), "9.9.0")
        self.assertEqual(code, 1)
        self.assertIn("newer than the table", msg)

    def test_non_version_agp_is_rejected_not_reported_supported(self):
        self.assertEqual(self.code((2026, 2, 2), ""), 2)
        self.assertEqual(self.code((2026, 2, 2), "latest"), 2)


class HelpersTest(unittest.TestCase):
    def test_lint_pairs_with_agp(self):
        self.assertEqual(studio.lint_for("9.5.1"), "32.5.1")
        self.assertEqual(studio.lint_for("9.4.0"), "32.4.0")
        self.assertEqual(studio.lint_for("nonsense"), "unknown")

    def test_min_studio_for(self):
        self.assertEqual(studio.min_studio_for((9, 4)), (2026, 1, 4))
        self.assertEqual(studio.min_studio_for((9, 5)), (2026, 2, 2))
        self.assertIsNone(studio.min_studio_for((9, 9)))


class DependenciesOfTest(unittest.TestCase):
    def screen(self, *results):
        return {"version": "1.0.0", "verdict": "FAIL", "results": list(results), "missing": []}

    def test_takes_the_highest_requirement_per_kind_and_names_who_asks(self):
        s = self.screen(
            {"coord": "androidx.core:core-ktx:1.19.0", "needs": [("compileSdk", "37")]},
            {"coord": "androidx.core:core:1.19.0", "needs": [("compileSdk", "38")]},
            {"coord": "io.coil-kt.coil3:coil:3.5.0", "needs": [("Kotlin", "2.4")]},
        )
        out = screen_gates.dependencies_of(s)
        self.assertIn("compileSdk 38 (needed by core-ktx, core)", out)
        self.assertIn("Kotlin 2.4 (needed by coil)", out)
        self.assertEqual(len(out), 2)

    def test_passing_results_contribute_nothing(self):
        self.assertEqual(screen_gates.dependencies_of(self.screen({"coord": "a:b:1", "needs": []})), [])

    def test_a_coordinate_without_colons_does_not_crash(self):
        out = screen_gates.dependencies_of(self.screen({"coord": "weird", "needs": [("Kotlin", "2.4")]}))
        self.assertEqual(out, ["Kotlin 2.4 (needed by weird)"])

    def test_long_requester_lists_are_trimmed(self):
        rs = [{"coord": f"g:art{i}:1", "needs": [("Kotlin", "2.4")]} for i in range(5)]
        self.assertIn("+2 more", screen_gates.dependencies_of(self.screen(*rs))[0])


if __name__ == "__main__":
    unittest.main()
