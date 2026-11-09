#!/usr/bin/env bash
# Push the current branch. On failure, rebase onto origin/main and retry.
# Three tries. Back off 5s after the first failure and 10s after the second.
set -euo pipefail

attempt=1
while true; do
  if git push; then
    exit 0
  fi
  if [ "$attempt" -ge 3 ]; then
    exit 1
  fi
  if [ "$attempt" -eq 1 ]; then
    sleep 5
  else
    sleep 10
  fi
  git fetch origin main
  git rebase origin/main
  attempt=$((attempt + 1))
done
