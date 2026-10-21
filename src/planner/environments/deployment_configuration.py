"""Budgets for application replacement and its readiness proof."""

# The first startup includes schema migration. The measured archive upgrade took
# 98.3 seconds before the remaining lifespan work, leaving little room in 120 seconds.
DEPLOYMENT_HEALTH_TIMEOUT_SECONDS = 300.0
