#!/usr/bin/env bash
#
# Deploy the CRM by pulling. Run this ON owen-main, from the checkout.
#
#   ./deploy.sh                     fast-forward the tracked branch and rebuild
#   ./deploy.sh --with-migrations   also allow new Alembic revisions to apply
#   ./deploy.sh --rebuild           rebuild even when nothing new came down
#
# Refuses to run on a dirty tree and refuses anything but a fast-forward, so a
# change made directly on the server is never silently reverted.
#
# Migrations are opt-in on purpose. `alembic upgrade head` is NOT in the api
# container's command, so it does not run on restart; it runs here, only when you
# ask. That makes a schema change a decision instead of a side effect of a deploy.
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"
ALLOW_MIGRATIONS=0
FORCE_REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --with-migrations) ALLOW_MIGRATIONS=1 ;;
    --rebuild)         FORCE_REBUILD=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

[ -f .env.prod ] || { echo ".env.prod is missing. Copy .env.example and fill it in." >&2; exit 1; }

say "Checking the working tree"
if [ -n "$(git status --porcelain)" ]; then
  echo "The server's tree has uncommitted changes:" >&2
  git status --short >&2
  echo >&2
  echo "Someone edited files directly on the server. Commit or discard them" >&2
  echo "before deploying, or the pull will refuse to overwrite them." >&2
  exit 1
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)
OLD=$(git rev-parse HEAD)
say "Fetching origin ($BRANCH)"
git fetch --quiet origin
NEW=$(git rev-parse "origin/$BRANCH")

if [ "$OLD" = "$NEW" ]; then
  if [ "$FORCE_REBUILD" -eq 0 ]; then
    echo "Already up to date at ${OLD:0:7}. Nothing to deploy."
    echo "Use --rebuild to rebuild the images anyway."
    exit 0
  fi
  echo "Already up to date at ${OLD:0:7}; rebuilding because --rebuild was given."
else
  if ! git merge-base --is-ancestor "$OLD" "$NEW"; then
    echo "origin/$BRANCH is not a fast-forward of what is deployed." >&2
    echo "History was rewritten, or the branch diverged. Sort that out first." >&2
    exit 1
  fi

  say "Incoming"
  git --no-pager log --oneline "$OLD..$NEW"

  MIGRATIONS=$(git diff --name-only --diff-filter=A "$OLD..$NEW" -- backend/migrations/versions/ || true)
  if [ -n "$MIGRATIONS" ]; then
    say "New database migrations"
    echo "$MIGRATIONS"
    if [ "$ALLOW_MIGRATIONS" -eq 0 ]; then
      echo >&2
      echo "This deploy carries schema changes. Re-run with --with-migrations" >&2
      echo "once you have a backup and know what they do." >&2
      exit 1
    fi
  fi

  say "Pulling"
  git merge --ff-only "origin/$BRANCH"
fi

say "Building"
$COMPOSE build

if [ "$ALLOW_MIGRATIONS" -eq 1 ]; then
  say "Applying migrations"
  # One-shot container: the schema is migrated before anything starts serving.
  $COMPOSE run --rm --no-deps -w /app/backend api alembic upgrade head
fi

say "Starting"
$COMPOSE up -d --remove-orphans

say "Waiting for health"
for i in $(seq 1 30); do
  if [ "$($COMPOSE ps --format json api | grep -c '"Health":"healthy"' || true)" -ge 1 ]; then
    echo "api is healthy"
    break
  fi
  [ "$i" -eq 30 ] && { echo "api did not become healthy" >&2; $COMPOSE logs --tail 40 api >&2; exit 1; }
  sleep 2
done

$COMPOSE ps
say "Deployed ${NEW:0:7}"
