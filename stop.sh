#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
exec bash ./stop.command "$@"
