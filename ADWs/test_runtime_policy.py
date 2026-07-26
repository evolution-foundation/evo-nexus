import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_policy import resolve_runtime, KILL_SWITCH_ENV  # noqa: E402


class TestRuntimePolicy(unittest.TestCase):
    def setUp(self):
        os.environ.pop(KILL_SWITCH_ENV, None)

    def test_unmigrated_workflow_defaults_to_claude(self):
        result = resolve_runtime("unknown-workflow", "task-1", policy={"workflows": {}})
        self.assertEqual(result["primary"], "claude")
        self.assertEqual(result["mode"], "off")

    def test_off_mode_stays_on_claude(self):
        policy = {"workflows": {"orch-review": {"mode": "off", "cohort_percent": 100}}}
        result = resolve_runtime("orch-review", "task-1", policy=policy)
        self.assertEqual(result["primary"], "claude")

    def test_shadow_mode_never_routes_primary_to_hermes(self):
        policy = {"workflows": {"orch-review": {"mode": "shadow", "cohort_percent": 100}}}
        result = resolve_runtime("orch-review", "task-1", policy=policy)
        self.assertEqual(result["primary"], "claude")
        self.assertEqual(result["mode"], "shadow")

    def test_canary_deterministic_for_same_cohort_key(self):
        policy = {"workflows": {"orch-review": {"mode": "canary", "cohort_percent": 50}}}
        first = resolve_runtime("orch-review", "task-42", policy=policy)
        second = resolve_runtime("orch-review", "task-42", policy=policy)
        self.assertEqual(first, second)

    def test_canary_100_percent_routes_all_to_hermes(self):
        policy = {"workflows": {"orch-review": {"mode": "canary", "cohort_percent": 100}}}
        for i in range(20):
            result = resolve_runtime("orch-review", f"task-{i}", policy=policy)
            self.assertEqual(result["primary"], "hermes")

    def test_canary_0_percent_routes_all_to_fallback(self):
        policy = {"workflows": {"orch-review": {"mode": "canary", "cohort_percent": 0, "fallback": "claude"}}}
        for i in range(20):
            result = resolve_runtime("orch-review", f"task-{i}", policy=policy)
            self.assertEqual(result["primary"], "claude")

    def test_kill_switch_overrides_everything(self):
        os.environ[KILL_SWITCH_ENV] = "1"
        policy = {"workflows": {"orch-review": {"mode": "canary", "cohort_percent": 100}}}
        result = resolve_runtime("orch-review", "task-1", policy=policy)
        self.assertEqual(result["primary"], "claude")
        self.assertEqual(result["reason"], "kill_switch")


if __name__ == "__main__":
    unittest.main()
