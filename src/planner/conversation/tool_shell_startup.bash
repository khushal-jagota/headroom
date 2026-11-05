# Panels sets BASH_ENV to this file for every agent it starts on Linux. Bash
# reads it before each non-interactive command, so tool shells and everything
# they start are killed before agents and Panels when memory runs out.
# A process may always raise its own score, so this never fails for permission.
{ echo 1000 > /proc/self/oom_score_adj; } 2>/dev/null || true
