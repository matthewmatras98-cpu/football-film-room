#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

dest="src/content/weeks/missing-field.mdx"
fixture="tests/fixtures/missing-field.mdx"
log="$(mktemp)"

cleanup() {
  rm -f "$dest"
  rm -f "$log"
}
trap cleanup EXIT

if [[ -e "$dest" ]]; then
  echo "refusing to overwrite $dest" >&2
  exit 1
fi

cp "$fixture" "$dest"

set +e
ASTRO_TELEMETRY_DISABLED=1 npm run build -- --force >"$log" 2>&1
status=$?
set -e

cat "$log"

if [[ "$status" -eq 0 ]]; then
  echo "expected astro build to fail when a required field is missing" >&2
  exit 1
fi

if ! grep -E -q 'summary|Invalid content entry|does not match schema|Content entry data' "$log"; then
  echo "astro build failed, but not because the missing-field fixture was rejected" >&2
  exit 1
fi

echo "astro build failed as expected (exit ${status})"
