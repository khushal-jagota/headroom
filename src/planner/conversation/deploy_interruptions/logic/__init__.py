"""Dependency-free interruption attribution rules."""

from planner.conversation.deploy_interruptions.contracts import CaptureState


def combine_capture_states(first: CaptureState, second: CaptureState) -> CaptureState:
    return "unavailable" if "unavailable" in (first, second) else "captured"


def matches_occurrence(
    captured_occurrence: str | None,
    current_occurrence: str | None,
    captured_epoch: str | None,
    current_epoch: str | None,
    current_status: str | None,
) -> bool:
    exact = (
        captured_occurrence == current_occurrence
        if captured_occurrence is not None
        else captured_epoch is not None and captured_epoch == current_epoch
    )
    return exact and current_status in ("intent", "running", "unavailable")


def claim_matches(
    *,
    exact: bool,
    captured_conversation: str,
    current_conversation: str | None,
    captured_revision: int | None,
    current_revision: int,
    captured_claim: str | None,
    current_claim: str,
    stage: str,
) -> bool:
    return (
        exact
        and stage != "done"
        and current_conversation == captured_conversation
        and captured_claim == current_claim == "out"
        and captured_revision is not None
        and captured_revision == current_revision
    )


def recovery_message_identity(interruption_ids: tuple[str, ...]) -> str:
    """One sender identity for the selected immutable interrupted occurrences."""
    import hashlib

    return (
        "deployment-recovery:"
        + hashlib.sha256("\n".join(sorted(interruption_ids)).encode()).hexdigest()
    )
