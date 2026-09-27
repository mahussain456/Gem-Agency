#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
exec python3 server.py
