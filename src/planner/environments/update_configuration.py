"""Cadence, bounds and names for self-installed updates."""

# Publication discovery and prefetch run on the existing readiness-loop cadence.
UPDATE_AVAILABILITY_REFRESH_SECONDS = 60.0

# The installer waits this long for exact-SHA health, once forward and once back.
# The first startup includes schema migration; a measured archive upgrade took 98 s.
UPDATE_HEALTH_TIMEOUT_SECONDS = 300.0

# Past this, the server settles an installing attempt from the SHA it runs. It covers
# a forward health wait, a database restore and a rollback health wait, with margin.
UPDATE_INSTALL_DEADLINE_SECONDS = 900.0

# After the VPS part is done, a Mac that reports nothing for this long is unreachable.
MAC_UPDATE_REPORT_SECONDS = 90.0

# How long `/api/deployment-status` (read by iOS builds in the field) shows success.
DEPLOYMENT_SUCCESS_DISPLAY_SECONDS = 300.0

# The live user unit the installer stops and starts.
LIVE_SERVICE_NAME = "panels-live.service"

# Staged and retained application folders beside `<current_root>/app`.
STAGED_APP_PREFIX = ".app-next-"
PREVIOUS_APP_PREFIX = ".app-previous-"

# The installer's copy in the data directory; no app rename can change the code it runs.
INSTALLER_FILE_NAME = "update-installer.py"
