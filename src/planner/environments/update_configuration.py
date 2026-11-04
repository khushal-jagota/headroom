"""Cadence for publication discovery on the existing readiness loop."""

UPDATE_AVAILABILITY_REFRESH_SECONDS = 60.0

# A finite recent history for one owner-controlled, serialized update flow.
# This deliberate bound is not a measured concurrency limit. Older evidence
# becomes unavailable, so delayed reports must retain explicit uncertainty.
MAX_CANCELLED_UPDATE_REPORTS = 32

# A durable attempt budget includes dispatch, download, replacement, and health proof.
UPDATE_ATTEMPT_TIMEOUT_SECONDS = 15 * 60.0
