#!/usr/bin/env sh
set -eu

action=${1:?usage: service-control.sh start|stop|restart|status}
case "$action" in
  start|stop|restart|status) ;;
  *) echo "unsupported service action: $action" >&2; exit 2 ;;
esac

if command -v launchctl >/dev/null 2>&1; then
  target=${PANELS_LAUNCHD_TARGET:-"gui/$(id -u)/com.panels.live"}
  case "$action" in
    start) exec /bin/launchctl kickstart "$target" ;;
    stop) exec /bin/launchctl kill "SIGTERM" "$target" ;;
    restart) exec /bin/launchctl kickstart -k "$target" ;;
    status) exec /bin/launchctl print "$target" ;;
  esac
fi
if command -v systemctl >/dev/null 2>&1; then
  exec systemctl "$action" panels-live.service
fi
echo "no supported service manager found" >&2
exit 1
