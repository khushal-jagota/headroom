"""Pure projection of saved provider observations into the existing Limits cards."""

from planner.conversation.backend_state import CachedBackendUsage
from planner.conversation.backend_usage import BackendUsageWindowKind
from planner.conversation.usage.contracts.providerUsageLimits import (
    ServerProviderUsageLimits,
    ServerProviderUsageWindow,
)


def presentProviderLimits(observation: CachedBackendUsage) -> ServerProviderUsageLimits:
    windows: list[ServerProviderUsageWindow] = []
    for window in observation.windows:
        session = window.kind is BackendUsageWindowKind.five_hour
        windows.append(
            {
                "id": f"{window.kind}:{window.model_scope or ''}",
                "kind": "session" if session else "weekly",
                "label": window.name,
                "usedPercent": window.used_percent,
                "resetsAt": window.resets_at.isoformat().replace("+00:00", "Z"),
                "windowDurationMins": 300 if session else 10080,
            }
        )
    return {
        "checkedAt": observation.observed_at.isoformat().replace("+00:00", "Z"),
        "windows": windows,
    }
