#!/usr/bin/env bash
# Sootprint setup: install project dependencies, then EXIT.
# Safe to re-run. Never installs system packages and never starts a server.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "==> Upgrading pip, setuptools and wheel"
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "==> Installing project dependencies (requirements.txt)"
"$PYTHON" -m pip install -r requirements.txt

echo "==> Installing the 'sootprint' command"
"$PYTHON" -m pip install -e .

echo "==> Verifying the tool imports and runs"
"$PYTHON" -c "import sootprint; assert callable(sootprint.main)"
"$PYTHON" -m sootprint --version

cat <<'DONE'

Setup complete. Nothing else to configure.

Try it now:
  sootprint --help
  bash demo.sh          # a full non-interactive tour on a throwaway log
  sootprint list        # your real log: $SOOTPRINT_HOME/sweeps.json
DONE
