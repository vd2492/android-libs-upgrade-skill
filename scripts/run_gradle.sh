#!/usr/bin/env bash
# Run Gradle with the output discipline this repo needs, and record Gradle's OWN exit code.
#
# Twice in one session a wrapper reported "exit code 0" for a Gradle run whose real exit was 1:
# the status belonged to the last command in the shell chain, not to Gradle. This script writes
# Gradle's exit code to a file the instant Gradle returns, so that file - not the shell, not the
# harness - is the answer, even when a long run is moved to the background.
#
# It also drops a start marker, so test_report.py can tell results this run produced from stale
# ones left by an earlier run (an UP-TO-DATE task writes no fresh XML and looks like a pass).
#
# Usage: run_gradle.sh <name> <gradle args...>
#   run_gradle.sh coil :base:testDebugUnitTest :core:common:testDebugUnitTest
#   -> build/lib-upgrade/runs/coil.{log,rc,start}
set -u
if [ "$#" -lt 2 ]; then
  echo "usage: run_gradle.sh <name> <gradle args...>" >&2
  exit 2
fi
name="$1"; shift
case "$name" in
  ""|.*|*[!A-Za-z0-9._-]*) echo "run name must be letters, digits, . _ - (and not start with a dot): '$name'" >&2; exit 2 ;;
esac
root="$(git rev-parse --show-toplevel 2>/dev/null)" || root=""
if [ -z "$root" ] || [ ! -d "$root" ]; then
  echo "not inside a git repository - run this from the Gradle project" >&2
  exit 2
fi
if [ ! -x "$root/gradlew" ]; then
  echo "no executable ./gradlew at $root" >&2
  exit 2
fi
dir="$root/build/lib-upgrade/runs"
mkdir -p "$dir"
log="$dir/$name.log"; rc="$dir/$name.rc"; start="$dir/$name.start"
rm -f "$rc"
touch "$start"
cd "$root" || exit 2
./gradlew "$@" --console=plain -q >"$log" 2>&1
code=$?
echo "$code" >"$rc"
echo "GRADLE EXIT=$code   (read from $rc; log: $log)"
grep -E "^e: |error:|FAILURE|FAILED|What went wrong" "$log" | head -30
exit "$code"
