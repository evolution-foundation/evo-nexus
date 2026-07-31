"""Regression: routines must exit non-zero when a step fails.

The scheduler decides routine health by returncode (scheduler.py: run_adw).
Before this fix, `runner.summary()` computed the failure count, printed
"N failure(s)" and returned None — every routine exited 0 even on failure,
so the scheduler logged a false success and never alerted.

Two guards, two failure modes:
1. `summary()` must RETURN the number of failed steps (fails if it goes
   back to returning None).
2. Every routine footer must PROPAGATE that value via sys.exit (fails if a
   footer goes back to calling summary() as a bare statement).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Routines shipped with the repo whose footer must propagate summary() -> exit code.
CORE_ROUTINES = [
    "ADWs/routines/backup.py",
    "ADWs/routines/end_of_day.py",
    "ADWs/routines/good_morning.py",
    "ADWs/routines/memory_lint.py",
    "ADWs/routines/memory_sync.py",
    "ADWs/routines/weekly_review.py",
]


def _load_runner():
    spec = importlib.util.spec_from_file_location("adw_runner", REPO_ROOT / "ADWs" / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summary_returns_failed_count():
    runner = _load_runner()
    ok = {"success": True, "duration": 1.0}
    bad = {"success": False, "duration": 1.0}
    assert runner.summary([ok, ok], "test") == 0
    assert runner.summary([ok, bad], "test") == 1
    assert runner.summary([bad, bad], "test") == 2


def _summary_calls(tree: ast.AST):
    """All Call nodes whose callee is `summary` (bare or attribute)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name == "summary":
                yield node


def _is_inside_sys_exit(tree: ast.AST, target: ast.Call) -> bool:
    """True if `target` appears inside the arguments of a sys.exit(...) call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        is_exit = (isinstance(fn, ast.Attribute) and fn.attr == "exit") or (
            isinstance(fn, ast.Name) and fn.id in ("exit", "SystemExit")
        )
        if not is_exit:
            continue
        for arg in node.args:
            if any(sub is target for sub in ast.walk(arg)):
                return True
    return False


def test_routine_footers_propagate_summary_result():
    offenders = []
    for rel in CORE_ROUTINES:
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(), filename=str(path))
        calls = list(_summary_calls(tree))
        assert calls, f"{rel}: expected at least one summary() call"
        for call in calls:
            if not _is_inside_sys_exit(tree, call):
                offenders.append(f"{rel}:{call.lineno}")
    assert not offenders, (
        "summary() result discarded (routine would exit 0 on failure): "
        + ", ".join(offenders)
    )
