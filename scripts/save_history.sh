#!/usr/bin/env bash
# Commit state/ and reports/ and push, retrying on a concurrent update. Exits non-zero if the push
# never succeeds, so the workflow run is marked failed instead of silently losing history.
set -uo pipefail
cd "$(dirname "$0")/.."
git config user.name  >/dev/null || git config user.name  "collector-bot"
git config user.email >/dev/null || git config user.email "collector-bot@users.noreply.github.com"
for p in state reports source_status.csv discovered_sources.csv US_Early_Careers_300_Companies.csv; do [ -e "$p" ] && git add "$p"; done
if git diff --cached --quiet; then
  echo "save_history: nothing to commit"; exit 0
fi
git commit -q -m "collector run $(date -u +%Y-%m-%dT%H:%MZ)"
branch="$(git rev-parse --abbrev-ref HEAD)"
for attempt in 1 2 3 4; do
  if git push -q origin "HEAD:${branch}"; then
    echo "save_history: pushed on attempt ${attempt}"; exit 0
  fi
  echo "save_history: push rejected (attempt ${attempt}); rebasing"
  git pull -q --rebase origin "${branch}" || { git rebase --abort 2>/dev/null; echo "save_history: rebase failed"; exit 1; }
  sleep $((attempt * 5))
done
echo "save_history: could not push after 4 attempts"; exit 1
