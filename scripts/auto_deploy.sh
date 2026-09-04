#!/bin/bash
# Pull upstream main when it moves and rebuild only the services whose
# inputs changed. The systemd timer in scripts/systemd/ runs this every two
# minutes on the EC2 host; scripts/deploy.sh runs it on demand. Safe to run
# by hand from the checkout.
#
# Why not `docker compose down && build --no-cache && up`: both Dockerfiles
# copy dependency manifests before source, so Docker's own cache already
# rebuilds the right layers when source changes. Stopping Caddy on every
# deploy dropped in-flight requests and forced a certificate reload for
# README-only merges.
set -euo pipefail
cd "$(dirname "$0")/.."

# One deploy at a time. A slow image build must not overlap the next tick.
command -v flock >/dev/null || { echo "flock is required (util-linux)" >&2; exit 1; }
exec 9>/tmp/auto-deploy.lock
flock -n 9 || { echo "another deploy is running"; exit 0; }

git fetch --quiet origin main
old=$(git rev-parse HEAD)
new=$(git rev-parse origin/main)
if [ "$old" = "$new" ]; then
  exit 0
fi

changed=$(git diff --name-only "$old" "$new")
services=()
grep -qE '^(Dockerfile|requirements/|policy_assistant/)' <<<"$changed" && services+=(api)
grep -qE '^web/' <<<"$changed" && services+=(web)
grep -qE '^(docker-compose\.yml|Caddyfile)$' <<<"$changed" && services=(api web caddy)

# A hand-edited checkout on the host is a problem to fix, not to deploy
# over. Stop before touching anything.
if ! git diff --quiet HEAD; then
  echo "checkout has local changes; refusing to deploy" >&2
  git status --short >&2
  exit 1
fi
git merge --ff-only --quiet origin/main
echo "deploy ${old:0:7} -> ${new:0:7}: ${services[*]:-nothing to rebuild}"
if [ ${#services[@]} -eq 0 ]; then
  exit 0
fi

# --pull refreshes base images so a Dependabot Docker bump takes effect.
# --no-deps keeps `up` from restarting Caddy when only api or web changed.
docker compose build --pull "${services[@]}"
docker compose up -d --no-deps "${services[@]}"
docker image prune -f >/dev/null

# The API port is not published and the image has no curl, so probe from
# inside the container. Uvicorn is up within a few seconds of the restart.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)" \
    2>/dev/null; then
    docker compose ps
    exit 0
  fi
  sleep 2
done
echo "api did not answer /api/health after the deploy" >&2
docker compose logs --tail 30 api >&2
exit 1
