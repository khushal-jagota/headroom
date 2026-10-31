"""Cadence for publication discovery on the existing readiness loop."""

UPDATE_AVAILABILITY_REFRESH_SECONDS = 60.0

# A finite recent history for one owner-controlled, serialized update flow.
# This deliberate bound is not a measured concurrency limit. Older evidence
# becomes unavailable, so delayed reports must retain explicit uncertainty.
MAX_CANCELLED_UPDATE_REPORTS = 32
