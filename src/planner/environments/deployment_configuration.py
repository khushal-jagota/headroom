"""Budgets for application replacement and its readiness proof."""

# The first startup includes schema migration. The measured archive upgrade took
# 98.3 seconds before the remaining lifespan work, leaving little room in 120 seconds.
DEPLOYMENT_HEALTH_TIMEOUT_SECONDS = 300.0

# Confirmation visibility is separate from the durable interruption record.
# Five minutes is provisional until the owner chooses the duration.
DEPLOYMENT_SUCCESS_DISPLAY_SECONDS = 300.0

# Detect expired report evidence even when no file changes at its deadline.
DEPLOYMENT_ATTENTION_POLL_SECONDS = 1.0
