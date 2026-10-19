#!/bin/bash
set -eu
cd -- "$(dirname -- "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10 or newer is required. Install Python from python.org."
  exit 1
fi
exec python3 scripts/setup.py "$@"
