"""Catalogue freshness and bounded cold-start policy."""

CATALOGUE_FRESH_SECONDS = 300
CATALOGUE_COLD_START_TIMEOUT_SECONDS = 35

# Destination-private proof records. Eviction requires a fresh observation.
CLAUDE_MAX_RETAINED_FINALIZATIONS = 128
