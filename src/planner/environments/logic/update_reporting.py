"""Evidence-only owner adoption report decisions."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planner.environments.deployment_lifecycle import DeploymentPhase


def final_report(
    *, cancelled: bool, committed: bool, serving_matches: bool,
    apply_outcome: str, provision_outcome: str, published_route_outcome: str,
) -> tuple[DeploymentPhase, str, str]:
    """A workflow result cannot establish an application health failure."""
    if cancelled:
        return ("cancelled", "update_cancelled",
                "Update canceled before installation. The previous version stayed active.")
    if committed:
        if (apply_outcome, provision_outcome, published_route_outcome) == (
            "success", "success", "success"
        ) and serving_matches:
            return ("succeeded", "deployment_succeeded",
                    "The requested application is installed and post-health checks completed.")
        checks = (("application reporting", apply_outcome),
                  ("agent skill provisioning", provision_outcome),
                  ("published HTTP/2", published_route_outcome))
        for name, outcome in checks:
            if outcome in {"failure", "cancelled"}:
                return ("failed", "workflow_check_failed",
                        f"The requested application is installed, but {name} did not complete.")
        return ("unknown", "deployment_evidence_uncertain",
                "The requested application is installed. Final check evidence is uncertain. "
                "Chief review is required. No action is needed from you.")
    return ("unknown", "admission_evidence_uncertain",
            "Update admission or installation evidence is uncertain. "
            "Work remains paused. Chief review is required.")
