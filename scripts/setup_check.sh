#!/bin/bash
# Doctor script — verifies every dependency and prints the exact install
# command for anything missing. Installs NOTHING. Config-dependent checks
# (paths, LoRA, templates, provider) live in `worker.py --dry-run`.
#
# Usage: ./scripts/setup_check.sh

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0

ok()   { printf ' \033[32m✅ %s\033[0m\n' "$1"; }
bad()  { printf ' \033[31m❌ %s\033[0m\n    → %s\n' "$1" "$2"; FAIL=1; }
warn() { printf ' \033[33m⚠️  %s\033[0m\n    → %s\n' "$1" "$2"; }

echo "avatar-pipeline setup check — $REPO_DIR"
echo
echo "── binaries ────────────────────────────────────────────────"
command -v python3 >/dev/null && ok "python3 ($(python3 -V 2>&1))" \
  || bad "python3" "install Xcode CLT or brew install python"
command -v ffmpeg  >/dev/null && ok "ffmpeg"   || bad "ffmpeg"   "brew install ffmpeg"
command -v ffprobe >/dev/null && ok "ffprobe"  || bad "ffprobe"  "brew install ffmpeg"
command -v yt-dlp  >/dev/null && ok "yt-dlp"   || bad "yt-dlp"   "brew install yt-dlp"
command -v exiftool >/dev/null && ok "exiftool" || bad "exiftool" "brew install exiftool"
command -v brctl   >/dev/null && ok "brctl (iCloud)" \
  || warn "brctl" "part of macOS; iCloud placeholder downloads won't work without it"

echo
echo "── python environment ──────────────────────────────────────"
if [ -x "$REPO_DIR/venv/bin/python" ]; then
  ok "venv exists ($REPO_DIR/venv)"
  for mod in yaml requests numbers_parser fastapi torch transformers; do
    if "$REPO_DIR/venv/bin/python" -c "import $mod" >/dev/null 2>&1; then
      ok "python module: $mod"
    else
      bad "python module: $mod" "$REPO_DIR/venv/bin/pip install -r requirements.txt"
    fi
  done
else
  bad "venv missing" "python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
fi

echo
echo "── services ────────────────────────────────────────────────"
curl -s -o /dev/null --max-time 3 "http://localhost:8188/system_stats" \
  && ok "ComfyUI API (localhost:8188)" \
  || bad "ComfyUI API (localhost:8188)" \
         "cd ~/ComfyUI && ./venv/bin/python main.py --listen 0.0.0.0 --port 8188"
curl -s -o /dev/null --max-time 3 "http://localhost:8189/health" \
  && ok "identity gate (localhost:8189)" \
  || bad "identity gate (localhost:8189)" \
         "$REPO_DIR/venv/bin/python $REPO_DIR/scripts/face_gate.py"
curl -s -o /dev/null --max-time 3 "http://localhost:5678/" \
  && ok "n8n (localhost:5678)" \
  || bad "n8n (localhost:5678)" "nvm exec 22 n8n   (see SETUP.md §6 — needs Node 22, not 26)"

echo
echo "── files & config (details via worker --dry-run) ──────────"
if [ -f "$REPO_DIR/config.yaml" ]; then
  ok "config.yaml"
  if [ -x "$REPO_DIR/venv/bin/python" ]; then
    echo "    running worker.py --dry-run for config/path/template checks…"
    "$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/worker.py" \
      --url "https://www.tiktok.com/@check/video/0" \
      --config "$REPO_DIR/config.yaml" --dry-run >/dev/null || FAIL=1
  fi
else
  bad "config.yaml" "cp config.example.yaml config.yaml && edit the paths"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "All checks passed — see SETUP.md for the first run."
else
  echo "Some checks failed — run the printed commands, then re-run this script."
fi
exit $FAIL
