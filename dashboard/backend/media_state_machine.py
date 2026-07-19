"""MediaJob state machine — explicit transition matrix (Feature: social-media-production).

SQLite CHECK constrains the *domain* of `media_jobs.status` values but has no
concept of valid transitions. This module is the single source of truth for
"can status X move to status Y" — every route that mutates `MediaJob.status`
must go through `assert_transition()` instead of writing the column directly,
mirroring how `tickets` routes never bypass `has_permission()`.
"""

from __future__ import annotations

MEDIA_JOB_STATES = (
    "queued",
    "preparing",
    "generating",
    "rendering",
    "validating",
    "ready_for_review",
    "rejected",
    "approved",
    "uploading",
    "creating_draft",
    "draft_created",
    "scheduling",
    "scheduled",
    "published",
    "retryable_failure",
    "failed",
    "cancelled",
)

# Terminal states — no outgoing transition except explicit re-queue handled
# by the route layer (a brand-new attempt_count cycle, not a raw transition).
TERMINAL_STATES = frozenset({"published", "failed", "cancelled"})

# Adjacency: state -> set of states it may transition into.
MEDIA_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"preparing", "cancelled", "failed"}),
    "preparing": frozenset({"generating", "retryable_failure", "failed", "cancelled"}),
    "generating": frozenset({"rendering", "retryable_failure", "failed", "cancelled"}),
    "rendering": frozenset({"validating", "retryable_failure", "failed", "cancelled"}),
    "validating": frozenset({"ready_for_review", "retryable_failure", "failed"}),
    "ready_for_review": frozenset({"approved", "rejected", "cancelled"}),
    "rejected": frozenset({"queued"}),  # explicit "recriar" re-queues from scratch
    "approved": frozenset({"uploading", "cancelled"}),
    "uploading": frozenset({"creating_draft", "retryable_failure", "failed"}),
    "creating_draft": frozenset({"draft_created", "retryable_failure", "failed"}),
    "draft_created": frozenset({"scheduling"}),
    "scheduling": frozenset({"scheduled", "retryable_failure", "failed"}),
    "scheduled": frozenset({"published", "retryable_failure", "failed"}),
    "published": frozenset(),
    "retryable_failure": frozenset({"preparing", "generating", "rendering", "validating",
                                     "uploading", "creating_draft", "scheduling", "failed", "cancelled"}),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

assert set(MEDIA_JOB_TRANSITIONS) == set(MEDIA_JOB_STATES)


class InvalidTransition(ValueError):
    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        allowed = sorted(MEDIA_JOB_TRANSITIONS.get(current, frozenset()))
        super().__init__(
            f"Cannot move MediaJob from '{current}' to '{target}'. "
            f"Allowed from '{current}': {allowed or '(terminal state)'}"
        )


def assert_transition(current: str, target: str) -> None:
    """Raise InvalidTransition if current -> target is not an allowed edge."""
    if target not in MEDIA_JOB_STATES:
        raise InvalidTransition(current, target)
    allowed = MEDIA_JOB_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransition(current, target)


def can_transition(current: str, target: str) -> bool:
    try:
        assert_transition(current, target)
        return True
    except InvalidTransition:
        return False


def allowed_targets(current: str) -> list[str]:
    return sorted(MEDIA_JOB_TRANSITIONS.get(current, frozenset()))
