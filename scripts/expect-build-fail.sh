#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

week_dest="src/content/weeks/missing-field.mdx"
game_dest="src/content/games/malformed.json"
game_out="dist-game-fail"

cleanup() {
  rm -f "$week_dest" "$game_dest"
  rm -rf "$game_out"
}
trap cleanup EXIT

run_expected_failure() {
  local dest="$1"
  local fixture="$2"
  local pattern="$3"
  local out_dir="${4:-}"
  local log
  log="$(mktemp)"

  if [[ -e "$dest" ]]; then
    echo "refusing to overwrite $dest" >&2
    rm -f "$log"
    exit 1
  fi

  cp "$fixture" "$dest"

  set +e
  if [[ -n "$out_dir" ]]; then
    ASTRO_TELEMETRY_DISABLED=1 npm run build -- --force --outDir "$out_dir" >"$log" 2>&1
  else
    ASTRO_TELEMETRY_DISABLED=1 npm run build -- --force >"$log" 2>&1
  fi
  local status=$?
  set -e

  cat "$log"

  if [[ "$status" -eq 0 ]]; then
    rm -f "$dest" "$log"
    if [[ -n "$out_dir" ]]; then
      rm -rf "$out_dir"
    fi
    echo "expected astro build to fail for $fixture" >&2
    exit 1
  fi

  if ! grep -E -q "$pattern" "$log"; then
    rm -f "$dest" "$log"
    if [[ -n "$out_dir" ]]; then
      rm -rf "$out_dir"
    fi
    echo "astro build failed, but not because $fixture was rejected" >&2
    exit 1
  fi

  rm -f "$dest" "$log"
  if [[ -n "$out_dir" ]]; then
    rm -rf "$out_dir"
  fi

  echo "astro build failed as expected (exit ${status}) for $fixture"
}

run_expected_failure \
  "$week_dest" \
  "tests/fixtures/missing-field.mdx" \
  "summary|Invalid content entry|does not match schema|Content entry data"

run_expected_failure \
  "$game_dest" \
  "tests/fixtures/games/malformed.json" \
  "malformed|Invalid content entry|does not match schema|Content entry data|game_id" \
  "$game_out"
