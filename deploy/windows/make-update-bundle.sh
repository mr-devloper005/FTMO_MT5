#!/usr/bin/env bash
# Mac side: package a code update for deploy.ps1 while github.com is blocked on the PC.
#
#   deploy/windows/make-update-bundle.sh <out-dir> [<commit running on the PC>]
#
# Writes <out-dir>/ftmo-update-<sha>.bundle (branch master). With the PC's current commit
# (C:\ftmo\logs\deploy.log or `git -C C:\ftmo\app rev-parse HEAD`) the bundle is incremental and
# small; without it the full history is included. If the package lock changed, offline wheels
# are added in <out-dir>/wheels (pass them to deploy.ps1 -Wheels).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?usage: make-update-bundle.sh <out-dir> [<pc-commit>]}"
BASE="${2:-}"
cd "$REPO"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "commit your changes first (only committed code is deployed)" >&2; exit 1
fi
mkdir -p "$OUT"
SHA="$(git rev-parse --short=10 master)"
FILE="$OUT/ftmo-update-$SHA.bundle"
if [ -n "$BASE" ]; then
  git bundle create "$FILE" "$BASE..master"
else
  git bundle create "$FILE" master
fi
git bundle verify "$FILE" >/dev/null 2>&1 || { [ -n "$BASE" ] && echo "(incremental bundle: verifies on the PC, which has $BASE)"; }
if [ -n "$BASE" ] && git diff --quiet "$BASE" master -- deploy/windows/requirements-research.lock.txt; then
  echo "packages unchanged - no wheels needed"
else
  echo "downloading Windows wheels (lock changed or unknown base)"
  "$REPO/venv/bin/python3" -m pip download -r deploy/windows/requirements-research.lock.txt --dest "$OUT/wheels" \
    --platform win_amd64 --python-version 3.12 --implementation cp --only-binary=:all: --quiet --disable-pip-version-check
fi
echo "bundle: $FILE"
echo "on the PC (admin PowerShell):  C:\\ftmo\\app\\deploy\\windows\\deploy.ps1 -Bundle <path>\\$(basename "$FILE") [-Wheels <path>\\wheels]"
