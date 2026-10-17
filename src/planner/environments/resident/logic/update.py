"""Cutover admission consumes executor-owned positive evidence only."""

from planner.environments.resident.contracts import ResidentPackage
from planner.executor.contracts import (
    ConnectionState,
    ExecutorHealth,
    ExecutorIdentity,
    Quiescence,
    QuiescenceReceipt,
)


def safe_quiescence(value: Quiescence) -> bool:
    """Reject malformed negative counts as well as nonzero/unknown work."""
    return (
        value.admission_stopped is True
        and value.idle_children_stopped is True
        and all(
            type(count) is int and count == 0
            for count in (
                value.active_turn_count,
                value.pending_callback_count,
                value.uncertain_operation_count,
                value.unsent_entry_count,
            )
        )
    )


def identity_matches(identity: ExecutorIdentity, package: ResidentPackage) -> bool:
    return (
        identity.application_revision == package.application_revision
        and identity.protocol_version == package.protocol_version
        and identity.spool_format_version == package.spool_format_version
        and bool(identity.executor_incarnation)
        and identity.connection_generation > 0
    )


def fresh_health(
    health: ExecutorHealth | None,
    package: ResidentPackage,
    *,
    not_before_ms: int,
    now_ms: int,
    previous_incarnation: str | None = None,
) -> bool:
    return (
        health is not None
        and identity_matches(health.identity, package)
        and health.process_id > 0
        and health.connection_state == ConnectionState.ready
        and health.reconciliation_ready is True
        and health.lost_at_ms is None
        and not_before_ms <= health.observed_at_ms <= now_ms
        and health.identity.executor_incarnation != previous_incarnation
    )


def matching_exit_receipt(health: ExecutorHealth, receipt: QuiescenceReceipt) -> bool:
    return (
        receipt.identity == health.identity
        and receipt.process_id == health.process_id
        and receipt.exit_committed is True
        and safe_quiescence(receipt.quiescence)
    )
