#!/usr/bin/env bash
# Copy the generated-card goldens into the content collection, build them,
# and confirm each game page exists. KC_DEN is the pending injury report.
set -euo pipefail

cd "$(dirname "$0")/.."

dest_dir="src/content/games"
out="dist-cards-check"
copied=()

cleanup() {
  local path
  for path in "${copied[@]}"; do
    rm -f "$path"
  done
  rm -rf "$out"
}
trap cleanup EXIT

shopt -s nullglob
files=(tests/fixtures/generated-cards/*.json)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "no generated card fixtures" >&2
  exit 1
fi

for src in "${files[@]}"; do
  base="$(basename "$src")"
  dest="$dest_dir/$base"
  if [[ -e "$dest" ]]; then
    echo "refusing to overwrite $dest" >&2
    exit 1
  fi
  cp "$src" "$dest"
  copied+=("$dest")
done

ASTRO_TELEMETRY_DISABLED=1 npm run build -- --force --outDir "$out"

for src in "${files[@]}"; do
  id="$(basename "$src" .json)"
  page="$out/games/$id/index.html"
  if [[ ! -f "$page" ]]; then
    echo "missing $page" >&2
    exit 1
  fi
done

pending="$out/games/2099_03_KC_DEN/index.html"
reported="$out/games/2099_03_CHI_DET/index.html"
if ! grep -q "Injury report not posted yet" "$pending"; then
  echo "KC_DEN is missing the pending injury line" >&2
  exit 1
fi
if grep -q "Injury report not posted yet" "$reported"; then
  echo "CHI_DET rendered the pending injury line" >&2
  exit 1
fi

echo "generated cards built; KC_DEN shows the pending injury line"
