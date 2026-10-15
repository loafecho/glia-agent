#!/usr/bin/env bash
# Prove every ladder guard is load-bearing: in a scratch copy of this repo, disable one guard at a time
# (scripts/red_mutate.py rewrites its body to `return None`) and require the test suite to go red.
# Output: one `guard=<name> disabled=FAIL-AS-EXPECTED` line per guard, then `RED-OK guards=K/K`.
# Any guard whose removal leaves the suite green prints `RED-BROKEN guard=<name>` and the script exits 1.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d /tmp/glia-prove-red-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
cp -r "$HERE/glia" "$HERE/tests" "$HERE/fixtures" "$HERE/pytest.ini" "$WORK/"
cp "$HERE/scripts/red_mutate.py" "$WORK/red_mutate.py"
cd "$WORK" || exit 1
if ! python3 -m pytest -q tests/ >"$WORK/baseline.log" 2>&1; then
  echo "RED-BROKEN baseline: the unmutated suite does not pass"; tail -5 "$WORK/baseline.log"; exit 1
fi
mapfile -t GUARDS < <(python3 red_mutate.py glia/ladder.py --list)
ok=0; broken=0
for g in "${GUARDS[@]}"; do
  cp glia/ladder.py "$WORK/ladder.orig"
  python3 red_mutate.py glia/ladder.py "$g" || { echo "RED-BROKEN guard=$g mutate-failed"; broken=$((broken+1)); cp "$WORK/ladder.orig" glia/ladder.py; continue; }
  if python3 -m pytest -q -x tests/ >"$WORK/$g.log" 2>&1; then
    echo "RED-BROKEN guard=$g disabled=SUITE-STILL-GREEN"; broken=$((broken+1))
  else
    failing=$(grep -c -E '^(FAILED|ERROR) ' "$WORK/$g.log")
    first=$(grep -m1 -E '^(FAILED|ERROR) ' "$WORK/$g.log" | sed -E 's/^(FAILED|ERROR) //; s/ - .*//')
    echo "guard=$g disabled=FAIL-AS-EXPECTED failing_tests=$failing first=$first"; ok=$((ok+1))
  fi
  cp "$WORK/ladder.orig" glia/ladder.py
done
if [ "$broken" -eq 0 ]; then echo "RED-OK guards=$ok/${#GUARDS[@]}"; exit 0; fi
echo "RED-FAIL guards=$ok/${#GUARDS[@]} broken=$broken"; exit 1
