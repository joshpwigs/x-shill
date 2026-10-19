#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
umask 077
test -x .venv/bin/python || { printf '%s\n' 'Run sh scripts/install.sh first.' >&2; exit 1; }
for candidate in chromium chromium-browser google-chrome google-chrome-stable; do
    if command -v "$candidate" >/dev/null 2>&1; then
        printf '%s\n' "Using system browser: $(command -v "$candidate")"
        printf '%s\n' 'Browser ready. Next: sh scripts/run.sh browser-login'
        exit 0
    fi
done
PLAYWRIGHT_BROWSERS_PATH="$PWD/data/ms-playwright" .venv/bin/python -m playwright install chromium
printf '%s\n' 'Chromium installed. Next: sh scripts/run.sh browser-login'
