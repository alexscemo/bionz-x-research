#!/usr/bin/env bash
# ILCE-6400 open-gate kit: selftest. See ../README.md.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../../../.." && pwd -P)"
PY=python3
[ -x venv/bin/python ] && PY=venv/bin/python
"$PY" -m tools.kits.opengate_a6400 selftest "$@"
