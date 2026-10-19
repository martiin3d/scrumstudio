#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
exec bash ./start.command "$@"
