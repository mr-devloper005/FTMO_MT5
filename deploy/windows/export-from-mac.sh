#!/usr/bin/env bash
# Mac side: build everything the Windows research PC needs, for a USB stick / shared drive.
#
#   deploy/windows/export-from-mac.sh <transfer-root> <secrets-dir>
#
#   <transfer-root>  e.g. /Volumes/FTMO-USB      -> <transfer-root>/ftmo-transfer-YYYYMMDD-HHMMSS/
#                    ftmo.bundle (code, full git history), research.db, research_state.zip,
#                    rag_state.zip (incl. the embedding model), ftmo_rag.dump, manifest.json (sha256),
#                    wheels/ (offline Windows packages), pgvector-0.8.7.zip, windows-scripts/
#   <secrets-dir>    a DIFFERENT place (second stick / password vault): .env.windows with the
#                    research keys only. Broker credentials (MT5_*, TT_*, WEBULL_*) are filtered out.
#
# Only committed code travels (git bundle): commit first. Nothing is uploaded anywhere.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?usage: export-from-mac.sh <transfer-root> <secrets-dir>}"
SECRETS="${2:?usage: export-from-mac.sh <transfer-root> <secrets-dir>}"
PY="$REPO/venv/bin/python3"
PGVECTOR=0.8.7
cd "$REPO"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Uncommitted changes in tracked files - commit them first (only committed code is exported):" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi
mkdir -p "$SECRETS"
case "$(cd "$SECRETS" 2>/dev/null && pwd)" in
  "$(mkdir -p "$OUT" && cd "$OUT" && pwd)"*) echo "keep <secrets-dir> outside <transfer-root>" >&2; exit 1 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "1/4 downloading Windows wheels for the pinned lock file"
"$PY" -m pip download -r deploy/windows/requirements-research.lock.txt --dest "$WORK/wheels" \
  --platform win_amd64 --python-version 3.12 --implementation cp --only-binary=:all: --quiet --disable-pip-version-check

echo "2/4 exporting code + data (+ checksum manifest)"
"$PY" -m research_agent transfer-export --out "$OUT" --secrets-dir "$SECRETS" --wheels "$WORK/wheels"
FOLDER="$(ls -d "$OUT"/ftmo-transfer-* | sort | tail -1)"

echo "3/4 pgvector $PGVECTOR source (github.com may be blocked on the PC)"
curl -fsSL -o "$FOLDER/pgvector-$PGVECTOR.zip" "https://github.com/pgvector/pgvector/archive/refs/tags/v$PGVECTOR.zip"

echo "4/4 setup scripts (so steps 0-3 can run before the code is cloned)"
mkdir -p "$FOLDER/windows-scripts"
cp deploy/windows/*.ps1 deploy/windows/*.md "$FOLDER/windows-scripts/"

if grep -Eq '^(MT5_|TT_|WEBULL_)' "$SECRETS/.env.windows" 2>/dev/null; then
  echo "BUG: broker key in $SECRETS/.env.windows - delete it and report" >&2; exit 1
fi
echo
echo "transfer folder : $FOLDER ($(du -sh "$FOLDER" | cut -f1))"
echo "secrets file    : $SECRETS/.env.windows (keys: $(cut -d= -f1 "$SECRETS/.env.windows" | tr '\n' ' '))"
echo "next            : deploy/windows/RUNBOOK_WINDOWS.md"
