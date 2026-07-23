#!/usr/bin/env sh
set -eu

action=${1:?usage: service-control.sh start|stop|restart|status}
case "$action" in
  start|stop|restart|status) ;;
  *) echo "unsupported service action: $action" >&2; exit 2 ;;
esac

if command -v launchctl >/dev/null 2>&1; then
  case "$action" in
    start) exec launchctl kickstart "system/com.panels.live" ;;
    stop) exec launchctl kill "SIGTERM" "system/com.panels.live" ;;
    restart) exec launchctl kickstart -k "system/com.panels.live" ;;
    status) exec launchctl print "system/com.panels.live" ;;
  esac
fi
if command -v systemctl >/dev/null 2>&1; then
  exec systemctl "$action" panels-live.service
fi
echo "no supported service manager found" >&2
exit 1
