#!/usr/bin/env bash
# Clean-room check: install the built wheel into a brand-new virtualenv (no [llm] extra), then run
# the self-test from a directory that is not the repository, with the interpreter in isolated
# mode (-I: no PYTHONPATH, no user site, no current directory on sys.path).
#
#   python -m build && bash scripts/clean_install.sh
#
# Environment knobs (for machines without PyPI access; the defaults are the real check):
#   PYTHON                          interpreter used to create the venv (default: python3)
#   CLEAN_INSTALL_PIP_ARGS          extra pip arguments, e.g. "--no-index --no-deps"
#   CLEAN_INSTALL_SYSTEM_SITE=1     create the venv with --system-site-packages (to find
#                                   pydantic/numpy when --no-index is used)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

shopt -s nullglob
wheels=("$ROOT"/dist/*.whl)
if (( ${#wheels[@]} != 1 )); then
    echo "clean_install: expected exactly one wheel in $ROOT/dist, found ${#wheels[@]} (run: python -m build)" >&2
    exit 2
fi
wheel="${wheels[0]}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

venv_args=()
if [[ -n "${CLEAN_INSTALL_SYSTEM_SITE:-}" ]]; then venv_args+=(--system-site-packages); fi
pip_args=()
if [[ -n "${CLEAN_INSTALL_PIP_ARGS:-}" ]]; then read -r -a pip_args <<< "$CLEAN_INSTALL_PIP_ARGS"; fi

"$PYTHON" -m venv ${venv_args[@]+"${venv_args[@]}"} "$work/venv"
py="$work/venv/bin/python"
"$py" -m pip install --quiet ${pip_args[@]+"${pip_args[@]}"} "$wheel"

cd "$work"  # not the repository

echo "== installed from the wheel, not from the repository"
"$py" -I - "$ROOT" "$work/venv" <<'PY'
import pathlib
import sys

import gao_idl

root, venv = (pathlib.Path(a).resolve() for a in sys.argv[1:3])
here = pathlib.Path(gao_idl.__file__).resolve()
assert venv in here.parents, f"gao_idl imported from {here}, expected inside {venv}"
assert root not in here.parents, f"gao_idl imported from the repository: {here}"
bad = [p for p in sys.path if p and root in (q := pathlib.Path(p).resolve(), *q.parents)]
assert not bad, f"repository on sys.path: {bad}"
print("gao_idl", gao_idl.__version__, "from", here)
PY

echo "== import hygiene (no httpx, hypothesis or pytest)"
"$py" -I -c "
import sys
import gao_idl, gao_idl.llm
bad = [m for m in ('httpx', 'hypothesis', 'pytest') if m in sys.modules]
assert not bad, bad
print('ok')"

echo "== selftest"
"$py" -I -c "import gao_idl; gao_idl.selftest()"

echo "clean_install: OK"
