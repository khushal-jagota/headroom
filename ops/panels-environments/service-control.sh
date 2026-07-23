#!/usr/bin/env sh
set -eu

action=${1:?usage: service-control.sh start|stop|restart|status}
case "$action" in
  start|stop|restart|status) ;;
  *) echo "unsupported service action: $action" >&2; exit 2 ;;
esac

if [ -n "${PANELS_LAUNCHCTL:-}" ] || command -v launchctl >/dev/null 2>&1; then
  launchctl=${PANELS_LAUNCHCTL:-/bin/launchctl}
  domain=${PANELS_LAUNCHD_DOMAIN:-"gui/$(id -u)"}
  label=${PANELS_LAUNCHD_LABEL:-com.panels.live}
  target=${PANELS_LAUNCHD_TARGET:-"$domain/$label"}
  plist=${PANELS_LAUNCHD_PLIST:-"$HOME/Library/LaunchAgents/$label.plist"}
  stop_loaded_job() {
    if "$launchctl" print "$target" >/dev/null 2>&1; then
      "$launchctl" kill SIGTERM "$target" >/dev/null 2>&1 || true
      attempts=0
      while "$launchctl" print "$target" 2>/dev/null | /usr/bin/grep -q 'pid ='; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 100 ]; then
          echo "service did not stop cleanly: $target" >&2
          exit 1
        fi
        sleep 0.1
      done
    fi
    "$launchctl" bootout "$target" >/dev/null 2>&1 || true
  }
  case "$action" in
    start)
      if "$launchctl" print "$target" >/dev/null 2>&1; then
        exec "$launchctl" kickstart "$target"
      fi
      exec "$launchctl" bootstrap "$domain" "$plist"
      ;;
    stop)
      stop_loaded_job
      exit 0
      ;;
    restart)
      stop_loaded_job
      exec "$launchctl" bootstrap "$domain" "$plist"
      ;;
    status) exec "$launchctl" print "$target" ;;
  esac
fi
if command -v systemctl >/dev/null 2>&1; then
  exec systemctl "$action" panels-live.service
fi
echo "no supported service manager found" >&2
exit 1
