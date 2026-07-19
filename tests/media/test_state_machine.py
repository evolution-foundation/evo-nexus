"""media_state_machine.py — transition matrix correctness."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

import pytest
from media_state_machine import (
    MEDIA_JOB_STATES, MEDIA_JOB_TRANSITIONS, InvalidTransition,
    assert_transition, can_transition, allowed_targets,
)


def test_every_state_has_a_transition_entry():
    assert set(MEDIA_JOB_TRANSITIONS) == set(MEDIA_JOB_STATES)


@pytest.mark.parametrize("current,target", [
    ("queued", "preparing"),
    ("preparing", "generating"),
    ("generating", "rendering"),
    ("rendering", "validating"),
    ("validating", "ready_for_review"),
    ("ready_for_review", "approved"),
    ("ready_for_review", "rejected"),
    ("rejected", "queued"),
    ("approved", "uploading"),
    ("uploading", "creating_draft"),
    ("creating_draft", "draft_created"),
    ("draft_created", "scheduling"),
    ("scheduling", "scheduled"),
    ("scheduled", "published"),
])
def test_valid_transitions_allowed(current, target):
    assert can_transition(current, target)
    assert_transition(current, target)  # must not raise


@pytest.mark.parametrize("current,target", [
    ("queued", "approved"),          # cannot skip the whole pipeline
    ("queued", "ready_for_review"),  # cannot fake completion
    ("draft_created", "scheduled"),  # must pass through scheduling
    ("ready_for_review", "scheduling"),  # cannot schedule before approval
    ("approved", "draft_created"),   # cannot skip upload/creating_draft
    ("published", "draft_created"),  # terminal state, no outgoing edges
    ("failed", "queued"),            # terminal state
    ("cancelled", "queued"),         # terminal state
    ("rendering", "rendering"),      # not a self-loop
    ("bogus_state", "queued"),       # unknown source state
])
def test_invalid_transitions_rejected(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransition):
        assert_transition(current, target)


def test_scheduling_never_reachable_before_validation():
    """Briefing: 'não permita agendar antes de validar'. `retryable_failure`
    is exempt — it is the resume-a-failed-attempt state, and a job that
    already passed through draft_created once before failing mid-schedule
    is allowed to resume directly into scheduling.
    """
    for state in MEDIA_JOB_STATES:
        if state in ("draft_created", "retryable_failure"):
            continue
        if can_transition(state, "scheduling"):
            pytest.fail(f"'{state}' should not be able to reach 'scheduling' directly")


def test_uploading_never_reachable_before_approval():
    """Briefing: 'não permita enviar ao Postiz antes da aprovação'.
    `retryable_failure` is exempt for the same resume-on-retry reason.
    """
    for state in MEDIA_JOB_STATES:
        if state in ("approved", "retryable_failure"):
            continue
        assert not can_transition(state, "uploading"), f"'{state}' should not reach 'uploading' directly"


def test_approved_never_reachable_without_review():
    """Briefing: 'não permita aprovar um job sem render' — approved is only
    reachable from ready_for_review, which itself is only reachable after
    validating.
    """
    sources = [s for s in MEDIA_JOB_STATES if can_transition(s, "approved")]
    assert sources == ["ready_for_review"]


def test_allowed_targets_matches_transition_map():
    assert allowed_targets("ready_for_review") == sorted(MEDIA_JOB_TRANSITIONS["ready_for_review"])
    assert allowed_targets("published") == []


def test_error_message_lists_allowed_targets():
    try:
        assert_transition("draft_created", "approved")
    except InvalidTransition as exc:
        assert "scheduling" in str(exc)
    else:
        pytest.fail("expected InvalidTransition")
