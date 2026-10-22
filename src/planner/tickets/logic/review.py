"""Eligibility for the owner's proposal review queue."""


def eligible_for_owner_review(
    *,
    ticket_status: str,
    is_backlogged: bool,
    holder_is_owner: bool,
    has_gating_field: bool,
    has_proposal: bool,
) -> bool:
    return (
        ticket_status == "awaiting_approval"
        and not is_backlogged
        and holder_is_owner
        and has_gating_field
        and has_proposal
    )
