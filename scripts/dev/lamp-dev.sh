#!/usr/bin/env bash
set -Eeuo pipefail

SESSION="${LAMP_TMUX_SESSION:-lamp-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
START_TIMEOUT="${LAMP_START_TIMEOUT:-120}"
AGENT_RUNTIME="${OS_AGENT_RUNTIME:-codex}"
case "$AGENT_RUNTIME" in
  codex) AGENT_PORT="${CODEX_PORT:-18792}" ;;
  claudecode) AGENT_PORT=18791 ;;
  *) echo "Unsupported development runtime: $AGENT_RUNTIME" >&2; exit 1 ;;
esac

usage() {
  cat <<'EOF'
Usage: scripts/dev/lamp-dev.sh [start|stop|restart|status|attach|logs]

Environment:
  LAMP_TMUX_SESSION   tmux session name (default: lamp-dev)
  LAMP_START_TIMEOUT  seconds to wait for each service (default: 120)
  SIM_MEDIA           virtual (default) or host
  DEVICE_TYPE         robot profile (default: lamp)
EOF
}

has_session() { tmux has-session -t "$SESSION" 2>/dev/null; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

port_owner() {
  ss -ltnp "sport = :$1" 2>/dev/null | awk 'NR > 1 { print; exit }'
}

wait_for_url() {
  local name="$1" url="$2" window="$3" elapsed=0
  while ! curl --silent --fail --output /dev/null "$url"; do
    if ! tmux list-panes -t "$SESSION:$window" -F '#{pane_dead}' 2>/dev/null | grep -qx 0; then
      echo "$name exited before becoming ready. Recent output:" >&2
      tmux capture-pane -p -t "$SESSION:$window" -S -30 >&2 || true
      return 1
    fi
    if (( elapsed >= START_TIMEOUT )); then
      echo "Timed out waiting for $name at $url. Recent output:" >&2
      tmux capture-pane -p -t "$SESSION:$window" -S -30 >&2 || true
      return 1
    fi
    sleep 1
    ((elapsed += 1))
  done
  echo "$name is ready: $url"
}

wait_for_port() {
  local name="$1" port="$2" window="$3" elapsed=0
  while [[ -z "$(port_owner "$port")" ]]; do
    if ! tmux list-panes -t "$SESSION:$window" -F '#{pane_dead}' 2>/dev/null | grep -qx 0; then
      echo "$name exited before opening port $port. Recent output:" >&2
      tmux capture-pane -p -t "$SESSION:$window" -S -30 >&2 || true
      return 1
    fi
    if (( elapsed >= START_TIMEOUT )); then
      echo "Timed out waiting for $name on port $port. Recent output:" >&2
      tmux capture-pane -p -t "$SESSION:$window" -S -30 >&2 || true
      return 1
    fi
    sleep 1
    ((elapsed += 1))
  done
  echo "$name is ready: port $port"
}

check_ports_free() {
  local port owner failed=0
  for port in 5000 5001 5173 "$AGENT_PORT"; do
    owner="$(port_owner "$port")"
    if [[ -n "$owner" ]]; then
      echo "Port $port is already in use: $owner" >&2
      failed=1
    fi
  done
  if (( failed )); then
    echo "Stop the existing services, then run '$0 start' again." >&2
    return 1
  fi
}

new_window() {
  local name="$1" command="$2"
  if ! has_session; then
    tmux new-session -d -s "$SESSION" -n "$name" -c "$REPO_ROOT" "$command"
    tmux set-option -g remain-on-exit on >/dev/null
  else
    tmux new-window -d -t "$SESSION" -n "$name" -c "$REPO_ROOT" "$command"
  fi
}

start() {
  require_command tmux
  require_command curl
  require_command ss
  require_command make

  if has_session; then
    echo "tmux session '$SESSION' is already running."
    status
    return 0
  fi
  check_ports_free

  echo "Preparing the shared OS state and development binary..."
  make -C "$REPO_ROOT" os-dev-build os-dev-seed
  trap 'if has_session; then echo "Startup failed; the tmux session was left available for logs." >&2; else echo "Startup failed before the tmux session was created." >&2; fi' ERR

  # tmux may predate this shell; explicitly carry the selected media profile.
  local hal_command="exec env" name value
  for name in SIM_MEDIA HAL_CAMERA_INDEX HAL_CAMERA_WIDTH HAL_CAMERA_HEIGHT HAL_AUDIO_INPUT_ALSA HAL_AUDIO_OUTPUT_DEVICE; do
    if [[ -v "$name" ]]; then
      printf -v value '%q' "$name=${!name}"
      hal_command+=" $value"
    fi
  done
  new_window hal "$hal_command make sim"
  wait_for_url "HAL simulator" "http://127.0.0.1:5001/docs" hal

  new_window "$AGENT_RUNTIME" "exec make ${AGENT_RUNTIME}-dev"
  wait_for_port "Agent bridge" "$AGENT_PORT" "$AGENT_RUNTIME"

  new_window os "exec make os-dev"
  wait_for_url "OS server" "http://127.0.0.1:5000/api/health/live" os

  new_window web "cd system/web && LAMP_PROXY=http://127.0.0.1:5000 exec npm run dev -- --host 0.0.0.0"
  wait_for_url "Web UI" "http://localhost:5173" web

  tmux select-window -t "$SESSION:os"
  trap - ERR
  echo
  echo "Lamp development stack is running in tmux session '$SESSION'."
  echo "Web UI: http://localhost:5173/monitor"
  echo "Attach: $0 attach"
  echo "Stop:   $0 stop"
}

stop() {
  if ! has_session; then
    echo "tmux session '$SESSION' is not running."
    return 0
  fi
  # Terminate service descendants before closing tmux; make can leave children
  # running after a pane receives SIGHUP, which blocks the next start.
  python3 - "$SESSION" <<'STOPPY'
import os, signal, subprocess, sys, time
roots = {int(p) for p in subprocess.check_output(
    ["tmux", "list-panes", "-s", "-t", sys.argv[1], "-F", "#{pane_pid}"], text=True).split()}
rows = [tuple(map(int, row.split())) for row in subprocess.check_output(
    ["ps", "-eo", "pid=,ppid="], text=True).splitlines()]
owned = set(roots)
while True:
    children = {pid for pid, parent in rows if parent in owned} - owned
    if not children:
        break
    owned.update(children)
for pid in owned - roots:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
for _ in range(100):
    living = []
    for pid in owned - roots:
        try:
            if open(f"/proc/{pid}/stat").read().split(") ", 1)[1][0] != "Z":
                living.append(pid)
        except FileNotFoundError:
            pass
    if not living:
        break
    time.sleep(0.1)
STOPPY
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  echo "Stopped Lamp development stack ('$SESSION')."
}

status() {
  local name port url state owner
  if has_session; then
    echo "tmux session: $SESSION (running)"
    tmux list-windows -t "$SESSION" -F '  #{window_name}: #{?pane_dead,exited,running}'
  else
    echo "tmux session: $SESSION (not running)"
  fi

  while IFS='|' read -r name port url; do
    owner="$(port_owner "$port")"
    if [[ -n "$owner" ]] && { [[ -z "$url" ]] || curl --silent --fail --output /dev/null "$url"; }; then
      state="ready"
    elif [[ -n "$owner" ]]; then
      state="listening, health check failed"
    else
      state="stopped"
    fi
    printf '%-13s :%-5s %s\n' "$name" "$port" "$state"
  done <<EOF
OS server|5000|http://127.0.0.1:5000/api/health/live
HAL simulator|5001|http://127.0.0.1:5001/docs
Web UI|5173|http://localhost:5173
Agent bridge|$AGENT_PORT|
EOF
}

attach() {
  has_session || { echo "tmux session '$SESSION' is not running." >&2; exit 1; }
  exec tmux attach-session -t "$SESSION"
}

logs() {
  has_session || { echo "tmux session '$SESSION' is not running." >&2; exit 1; }
  local window
  for window in hal "$AGENT_RUNTIME" os web; do
    echo "===== $window ====="
    tmux capture-pane -p -t "$SESSION:$window" -S -40 2>/dev/null || echo "(window unavailable)"
  done
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  attach) attach ;;
  logs) logs ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
