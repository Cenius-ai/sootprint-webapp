#!/usr/bin/env bash
# Sootprint end-to-end demo: log sweeps, read the log, draw the chart, score the
# risk, and show the two failure paths. Non-interactive and safe to re-run - it
# works entirely inside a throwaway directory and never touches your real log.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
DEMO_HOME="$(mktemp -d)"
trap 'rm -rf "$DEMO_HOME"' EXIT
export SOOTPRINT_HOME="$DEMO_HOME"

step() { printf '\n=== %s ===\n' "$1"; }
show() {
  printf '$'
  for arg in "$@"; do
    case "$arg" in
      *" "*) printf ' "%s"' "$arg" ;;
      *) printf ' %s' "$arg" ;;
    esac
  done
  printf '\n'
  "$@"
}

step "Store: a throwaway directory for this demo"
echo "SOOTPRINT_HOME=$SOOTPRINT_HOME"

step "1/8  An empty log invites the first sweep"
show "$PYTHON" sootprint.py list

step "2/8  Log a sweep"
show "$PYTHON" sootprint.py add --date 2024-01-15 --condition 2 \
  --flue "main flue" --notes "heavy soot on the smoke shelf"
show "$PYTHON" sootprint.py add --date 2024-03-02 --condition 3 \
  --flue "main flue" --notes "baffle scraped, flue sound"
show "$PYTHON" sootprint.py add --date 2024-04-20 --condition 4 \
  --flue "rear flue" --notes "light ash only"
show "$PYTHON" sootprint.py add --date 2024-10-05 --condition 2 \
  --flue "main flue" --notes "creosote glaze above the damper"

step "3/8  Read the log back, newest first"
show "$PYTHON" sootprint.py list

step "4/8  Draw the flue condition chart"
show "$PYTHON" sootprint.py chart

step "5/8  Score the creosote risk"
show "$PYTHON" sootprint.py risk

step "6/8  Bad input is refused: nothing is written (exit status 2)"
if "$PYTHON" sootprint.py add --date 15/01/2024 --condition 4; then
  echo "unexpected success" >&2
  exit 1
fi
if "$PYTHON" sootprint.py add --date 2024-05-01 --condition 9; then
  echo "unexpected success" >&2
  exit 1
fi
echo "the log still holds exactly four sweeps:"
"$PYTHON" sootprint.py list | tail -n 1

step "7/8  An unknown command prints usage on stderr and exits non-zero"
if "$PYTHON" sootprint.py frobnicate 2>/dev/null; then
  echo "unexpected success" >&2
  exit 1
fi
echo "(usage summary was written to stderr)"

step "8/8  The whole log is one human-readable JSON file"
echo "$SOOTPRINT_HOME/sweeps.json"
head -n 12 "$SOOTPRINT_HOME/sweeps.json"

printf '\nDemo complete. Your own log was not touched.\n'
