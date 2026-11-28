#!/usr/bin/env bash
# ILCE-6400 open-gate kit: apply. See ../README.md.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../../../.." && pwd -P)"
PY=python3
[ -x venv/bin/python ] && PY=venv/bin/python
MOD="${1:-artifacts/tests/opengate-a1/OpenGateA1.mod}"
"$PY" -m tools.kits.opengate_a6400 apply --mod "$MOD"
