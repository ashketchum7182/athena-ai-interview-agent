"""
Focused unit tests for the planner, including edge cases the real
candidate data doesn't happen to exercise (very small mission lists,
unknown curriculum days). Uses stdlib unittest -- no pytest dependency.

Run with:
    python3 tests/test_planner.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from agent.planner import build_plan

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load_curriculum():
    with open(os.path.join(ROOT, "backend", "data", "curriculum.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load_candidates():
    with open(os.path.join(ROOT, "candidates.json"), "r", encoding="utf-8") as f:
        return json.load(f)["candidates"]


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.curriculum = load_curriculum()

    def test_all_real_candidates_meet_minimums(self):
        for cand in load_candidates():
            plan = build_plan(cand, self.curriculum)
            days = {t.day for t in plan}
            with self.subTest(candidate=cand["member"]["id"]):
                self.assertGreaterEqual(len(plan), 8, "must plan >= 8 topics")
                self.assertGreaterEqual(len(days), 4, "must cover >= 4 distinct days")

    def test_minimal_candidate_with_exactly_four_missions(self):
        """Edge case: a candidate with the bare minimum of valid missions."""
        candidate = {
            "member": {"id": "X", "name": "Minimal Candidate", "jobRole": "Engineer", "yearsExperience": 2},
            "missions": [
                {"day": 7, "title": "Embeddings Explained", "passed": True, "attempts": 1},
                {"day": 12, "title": "Prompt Engineering Fundamentals", "passed": True, "attempts": 1},
                {"day": 22, "title": "Multi-Agent Orchestration", "passed": True, "attempts": 1},
                {"day": 31, "title": "Capstone Project & Final Demo", "passed": True, "attempts": 1},
            ],
        }
        plan = build_plan(candidate, self.curriculum)
        days = {t.day for t in plan}
        self.assertEqual(len(days), 4)
        # Capstone should be pinned to the end of the narrative order.
        self.assertEqual(plan[-1].day, 31)

    def test_unknown_curriculum_days_are_skipped_gracefully(self):
        candidate = {
            "member": {"id": "X", "name": "Weird Candidate", "jobRole": "Engineer", "yearsExperience": 2},
            "missions": [
                {"day": 999, "title": "Does Not Exist", "passed": True, "attempts": 1},
                {"day": 7, "title": "Embeddings Explained", "passed": True, "attempts": 1},
                {"day": 8, "title": "Vector Databases Overview", "passed": True, "attempts": 1},
                {"day": 12, "title": "Prompt Engineering Fundamentals", "passed": True, "attempts": 1},
                {"day": 31, "title": "Capstone Project & Final Demo", "passed": True, "attempts": 1},
            ],
        }
        plan = build_plan(candidate, self.curriculum)
        self.assertNotIn(999, {t.day for t in plan})
        self.assertTrue(all(1 <= t.day <= 31 for t in plan))

    def test_failed_and_skipped_missions_are_prioritized(self):
        """A failed/skipped mission should outrank an easy first-try pass
        of similar curriculum-day type, since it's the more informative
        thing to probe in an interview."""
        candidate = {
            "member": {"id": "X", "name": "Test", "jobRole": "Engineer", "yearsExperience": 3},
            "missions": [
                {"day": 7, "title": "Embeddings Explained", "passed": False, "attempts": 5},
                {"day": 8, "title": "Vector Databases Overview", "passed": True, "attempts": 1},
                {"day": 10, "title": "Retrieval & Matching Engine", "passed": True, "attempts": 1},
                {"day": 12, "title": "Prompt Engineering Fundamentals", "passed": True, "attempts": 1},
                {"day": 16, "title": "Chatbot Backend & API Integration", "passed": True, "attempts": 1},
            ],
        }
        plan = build_plan(candidate, self.curriculum)
        day7 = next(t for t in plan if t.day == 7)
        self.assertIn("did not pass", day7.reason.lower())

    def test_setup_days_deprioritized_when_alternatives_exist(self):
        candidate = {
            "member": {"id": "X", "name": "Test", "jobRole": "Engineer", "yearsExperience": 3},
            "missions": [{"day": 1, "title": "VS Code Setup", "passed": True, "attempts": 1}]
            + [
                {"day": d, "title": t, "passed": True, "attempts": 1}
                for d, t in [
                    (7, "Embeddings Explained"),
                    (8, "Vector Databases Overview"),
                    (10, "Retrieval & Matching Engine"),
                    (12, "Prompt Engineering Fundamentals"),
                    (16, "Chatbot Backend & API Integration"),
                    (22, "Multi-Agent Orchestration"),
                    (23, "Model Context Protocol (MCP)"),
                    (28, "Docker & Kubernetes Deployment"),
                    (31, "Capstone Project & Final Demo"),
                ]
            ],
        }
        plan = build_plan(candidate, self.curriculum)
        # 9 non-setup missions >= target, so day 1 (SETUP) should be dropped.
        self.assertNotIn(1, {t.day for t in plan})


if __name__ == "__main__":
    unittest.main(verbosity=2)
