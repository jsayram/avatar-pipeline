#!/usr/bin/env bash
# Render ops/launchd/*.plist.example into real launchd plists using the
# values in the git-ignored .env, then copy them to ~/Library/LaunchAgents.
#
#   ops/install_launchd.sh                 render + copy all four services
#   ops/install_launchd.sh --render-only   render into ops/launchd/generated/ only
#   ops/install_launchd.sh gate n8n        only the named services
#
# This script NEVER loads, unloads or starts anything. It prints the
# launchctl commands; run them yourself when you want the services up.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/ops/launchd"
OUT="$SRC/generated"            # git-ignored
AGENTS="$HOME/Library/LaunchAgents"

render_only=0
services=()
for arg in "$@"; do
  case "$arg" in
    --render-only) render_only=1 ;;
    comfyui|dashboard|gate|n8n) services+=("$arg") ;;
    *) echo "usage: $0 [--render-only] [comfyui|dashboard|gate|n8n ...]" >&2; exit 2 ;;
  esac
done
[ ${#services[@]} -gt 0 ] || services=(comfyui dashboard gate n8n)

# Read KEY=VALUE from .env without executing it (the file also holds secrets).
env_get() {
  local line
  line="$(grep -E "^[[:space:]]*$1=" "$ROOT/.env" 2>/dev/null | tail -n1 || true)"
  line="${line#*=}"
  line="${line%\"}"; line="${line#\"}"
  line="${line%\'}"; line="${line#\'}"
  printf '%s' "$line"
}
need() {   # need KEY  -> value, or exit naming the missing key
  local v; v="$(env_get "$1")"
  if [ -z "$v" ]; then echo "error: $1 is not set in $ROOT/.env (see .env.example)" >&2; exit 1; fi
  printf '%s' "$v"
}

[ -f "$ROOT/.env" ] || { echo "error: $ROOT/.env not found (copy .env.example)" >&2; exit 1; }

LABEL_PREFIX="$(need LAUNCHD_LABEL_PREFIX)"
LOG_DIR="$HOME/Library/Logs"   # the dashboard log tail reads this same folder
HOMEBREW_BIN="$(env_get HOMEBREW_BIN)"
if [ -z "$HOMEBREW_BIN" ]; then
  if command -v brew >/dev/null 2>&1; then HOMEBREW_BIN="$(brew --prefix)/bin"; else HOMEBREW_BIN="/usr/local/bin"; fi
fi
COMFYUI_DIR=""; NODE_BIN_DIR=""; N8N_WEBHOOK_URL=""
for s in "${services[@]}"; do
  case "$s" in
    comfyui) COMFYUI_DIR="$(need COMFYUI_DIR)" ;;
    n8n)     NODE_BIN_DIR="$(need NODE_BIN_DIR)"; N8N_WEBHOOK_URL="$(need N8N_WEBHOOK_URL)" ;;
  esac
done

mkdir -p "$OUT" "$LOG_DIR"
for s in "${services[@]}"; do
  content="$(cat "$SRC/$s.plist.example")"
  content="${content//__LABEL_PREFIX__/$LABEL_PREFIX}"
  content="${content//__PROJECT_ROOT__/$ROOT}"
  content="${content//__HOME__/$HOME}"
  content="${content//__LOG_DIR__/$LOG_DIR}"
  content="${content//__HOMEBREW_BIN__/$HOMEBREW_BIN}"
  content="${content//__COMFYUI_DIR__/$COMFYUI_DIR}"
  content="${content//__NODE_BIN_DIR__/$NODE_BIN_DIR}"
  content="${content//__N8N_WEBHOOK_URL__/$N8N_WEBHOOK_URL}"
  file="$OUT/$LABEL_PREFIX.$s.plist"
  printf '%s\n' "$content" > "$file"
  plutil -lint "$file" >/dev/null
  echo "rendered $file"
  if [ "$render_only" -eq 0 ]; then
    mkdir -p "$AGENTS"
    cp "$file" "$AGENTS/"
    echo "copied   $AGENTS/$LABEL_PREFIX.$s.plist"
  fi
done

echo
echo "Nothing was loaded or started. To (re)start a service yourself:"
for s in "${services[@]}"; do
  echo "  launchctl bootstrap gui/\$(id -u) $AGENTS/$LABEL_PREFIX.$s.plist   # first time"
  echo "  launchctl kickstart -k gui/\$(id -u)/$LABEL_PREFIX.$s              # after a re-render"
done
