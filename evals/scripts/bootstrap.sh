#!/usr/bin/env bash
# Bootstrap the isolated evaluation venv.
#
# Creates evals/.venv, installs the production project as an editable path dependency
# (root -> prod deps + `lib` importable), then installs this eval project (deepeval).
# Re-runnable: safe to call again to refresh.
#
# Usage:  bash evals/scripts/bootstrap.sh [PYTHON_BIN]
#   PYTHON_BIN defaults to python3.12 (falls back to python3). deepeval 4.2 supports
#   >=3.9,<4.0; 3.12 is the safest interpreter for the heavy transitive tree.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # evals/
ROOT="$(cd "$HERE/.." && pwd)"                            # repo root
PYTHON_BIN="${1:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then PYTHON_BIN=python3.12
  else PYTHON_BIN=python3; fi
fi

VENV="$HERE/.venv"
echo ">> Using interpreter: $($PYTHON_BIN --version 2>&1) ($PYTHON_BIN)"
echo ">> Creating venv at: $VENV"
"$PYTHON_BIN" -m venv "$VENV"

PIP="$VENV/bin/pip"
"$PIP" install --upgrade pip >/dev/null

echo ">> Installing production project (editable path dependency: root -> prod deps + lib)"
"$PIP" install -e "$ROOT"

echo ">> Installing eval project (deepeval + harness)"
"$PIP" install -e "$HERE"

echo ">> Verifying imports"
"$VENV/bin/python" - <<'PY'
import lib
import deepeval
from deepeval.integrations.llama_index import instrument_llama_index  # noqa: F401
from llama_index.core.instrumentation import get_dispatcher  # noqa: F401
print(f"OK  lib={lib.__file__}")
print(f"OK  deepeval={deepeval.__version__}")
print("OK  instrument_llama_index + get_dispatcher importable")
PY

echo ">> Done. Activate with:  source $VENV/bin/activate"
