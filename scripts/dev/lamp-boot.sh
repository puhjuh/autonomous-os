#!/usr/bin/env bash
# Boot the installed Pi stack without builds, downloads, or credential changes.
set -Eeuo pipefail
export PATH="/home/pj/.nvm/versions/node/v24.20.0/bin:/home/pj/.local/bin:/usr/local/bin:/usr/bin:/bin"
OS_REPO=/home/pj/Documents/lamp/autonomous-os
STATE=/home/pj/.local/state/lamp/autonomous-os
wait_for() {
  local url="$1"
  for ((i=0;i<120;i++)); do
    curl --max-time 2 --silent --fail --output /dev/null "$url" && return 0
    sleep 1
  done
  echo "Service did not become ready: $url" >&2
  return 1
}
case "${1:-start}" in
  run)
    component="${2:?component required}"
    [[ ! -f "/home/pj/.config/lamp/$component.env" ]] || source "/home/pj/.config/lamp/$component.env"
    case "$component" in
      hal)
        # CSI cameras are selected by libcamera, not a /dev/video index.
        if [[ "${HAL_SIM_CAMERA_DRIVER:-host}" != "rpicam" ]]; then
          camera_device="/dev/video${HAL_CAMERA_INDEX:-0}"
          for ((i=0;i<10;i++)); do [[ -e "$camera_device" ]] && break; sleep 1; done
          [[ -e "$camera_device" ]] || echo "Camera $camera_device unavailable; starting HAL with its media fallback." >&2
        fi
        cd "$OS_REPO/hal"
        exec .venv/bin/uvicorn hal.server:app --host 0.0.0.0 --port 5001
        ;;
      agent) cd "$OS_REPO"; exec "$STATE/os-server" claudecode-gatewayd ;;
      os) wait_for http://127.0.0.1:5001/docs; cd "$STATE"; exec ./os-server ;;
      web) wait_for http://127.0.0.1:5000/api/health/live; cd "$OS_REPO/system/web"; export LAMP_PROXY=http://127.0.0.1:5000; exec npm run dev -- --host 0.0.0.0 --strictPort ;;
      *) echo "Unknown component: $component" >&2; exit 2 ;;
    esac
    ;;
  start|stop|restart) systemctl --user "$1" lamp-stack.target ;;
  status) systemctl --user --no-pager status lamp-stack.target 'lamp-component@*.service' ;;
  logs) journalctl --user-unit='lamp-component@*' -n 100 --no-pager ;;
  *) echo 'Usage: lamp-boot.sh [start|stop|restart|status|logs]' >&2; exit 2 ;;
esac
