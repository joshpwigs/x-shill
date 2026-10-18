#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
umask 077
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ is required"'
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install --no-input --disable-pip-version-check --cache-dir .venv/pip-cache -r requirements-dev.txt
if [ ! -e config.toml ]; then cp config.example.toml config.toml; fi
.venv/bin/python -m shill check
printf '%s\n' 'Installed. Start with: sh scripts/run.sh'
