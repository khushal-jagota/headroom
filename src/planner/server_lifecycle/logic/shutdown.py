"""A fixed launch policy gates each kernel-attributed shutdown signal."""

from planner.server_lifecycle.contracts import (
    ProcessIdentity,
    ShutdownDecision,
    ShutdownPolicy,
)


def select_shutdown_policy(
    parent_pid: int,
    parent_identity: ProcessIdentity | None,
    *,
    parent_is_systemd: bool,
) -> ShutdownPolicy:
    if parent_identity is None or parent_identity.pid != parent_pid:
        # Unknown launch identity fails closed, with nobody authorized.
        return ShutdownPolicy("guarded", parent_pid, None)
    if parent_is_systemd:
        return ShutdownPolicy("guarded", parent_pid, parent_identity)
    return ShutdownPolicy("dogfood", parent_pid, None)


def decide_shutdown(
    policy: ShutdownPolicy,
    *,
    sender_pid: int | None,
    current_parent_pid: int,
    manager_identity: ProcessIdentity | None,
) -> ShutdownDecision:
    if policy.mode == "dogfood":
        return ShutdownDecision(True, "ordinary launch parent")
    if policy.authorized_manager is None:
        return ShutdownDecision(False, "launch parent identity was not verifiable")
    if sender_pid is None or sender_pid <= 0:
        return ShutdownDecision(False, "kernel sender identity is unavailable")
    if current_parent_pid != policy.launch_parent_pid:
        return ShutdownDecision(False, "parent changed since launch")
    if sender_pid != policy.authorized_manager.pid:
        return ShutdownDecision(False, "sender is not the authorized systemd manager")
    if manager_identity is None:
        return ShutdownDecision(False, "systemd manager identity is unavailable")
    if manager_identity != policy.authorized_manager:
        return ShutdownDecision(False, "systemd manager identity changed since launch")
    return ShutdownDecision(True, "verified systemd manager")
