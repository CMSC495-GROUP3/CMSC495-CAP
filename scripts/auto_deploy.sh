#!/bin/bash
# Pull upstream main when it moves and rebuild only the services whose
# inputs changed. The systemd timer in scripts/systemd/ runs this every two
# minutes on the EC2 host; scripts/deploy.sh runs it on demand. Safe to run
# by hand from the checkout, as the deploying user rather than root.
#
# Why not `docker compose down && build --no-cache && up`: both Dockerfiles
# copy dependency manifests before source, so Docker's own cache already
# rebuilds the right layers when source changes. Stopping Caddy on every
# deploy dropped in-flight requests and forced a certificate reload for
# README-only merges.
#
# Progress is tracked in refs/deployed/main, not HEAD. The script
# fast-forwards HEAD before building, so a failed build would otherwise look
# like "nothing to deploy" on the next tick while the stack stayed stale.
# After AUTO_DEPLOY_MAX_FAILURES consecutive failures on the same commit
# (default 5; 0 disables the cap), the tick stays red without rebuilding.
#
# Everything lives in main() so bash parses the whole file before running
# any of it: the fast-forward below replaces this very file.
set -euo pipefail

DEPLOYED_REF=refs/deployed/main
FAILURE_STATE_FILE=.git/auto-deploy-failures
# Override on the host if a broken commit should keep retrying (0) or give up sooner.
MAX_FAILURES=${AUTO_DEPLOY_MAX_FAILURES:-5}
# Never auto-load an untracked docker-compose.override.yml from the host.
COMPOSE_FILE=docker-compose.yml
export COMPOSE_FILE

# Nginx answers /api/health by proxying to Uvicorn, so one request through
# the web container checks both services and the hop between them. The API
# port is not published and neither image has curl; nginx:alpine ships
# BusyBox wget, which exits non-zero on a 5xx. The address is 127.0.0.1, not
# localhost: BusyBox resolves localhost to ::1 first, and web/nginx.conf
# listens on IPv4 only, so the name form is refused on every attempt.
healthy() {
  docker compose exec -T web wget -qO /dev/null http://127.0.0.1/api/health 2>/dev/null
}

mark_deployed() {
  local tip=$1
  git update-ref "$DEPLOYED_REF" "$tip"
  rm -f "$FAILURE_STATE_FILE"
}

failure_count_for() {
  local tip=$1
  local prev_sha prev_count
  if [ -f "$FAILURE_STATE_FILE" ]; then
    read -r prev_sha prev_count < "$FAILURE_STATE_FILE" || true
    if [ "${prev_sha:-}" = "$tip" ]; then
      echo "${prev_count:-0}"
      return
    fi
  fi
  echo 0
}

bump_failure() {
  local tip=$1
  local count=1
  local prev_sha prev_count
  if [ -f "$FAILURE_STATE_FILE" ]; then
    read -r prev_sha prev_count < "$FAILURE_STATE_FILE" || true
    if [ "${prev_sha:-}" = "$tip" ]; then
      count=$((prev_count + 1))
    fi
  fi
  echo "$tip $count" > "$FAILURE_STATE_FILE"
}

main() {
  cd "$(dirname "$0")/.."

  # One deploy at a time: a manual run must not overlap the timer (systemd
  # itself never starts a second instance of a oneshot that is still
  # running). The lock is on the checkout directory, so nothing is created
  # and a run under sudo cannot leave a root-owned lock file behind.
  command -v flock >/dev/null || { echo "flock is required (util-linux)" >&2; exit 1; }
  exec 9<.
  flock -n 9 || { echo "another deploy is running"; exit 0; }

  branch=$(git symbolic-ref --short -q HEAD || true)
  if [ "$branch" != main ]; then
    echo "checkout is on ${branch:-a detached HEAD}, not main; refusing to deploy" >&2
    exit 1
  fi

  git fetch --quiet origin main
  # A hand-edited checkout on the host is a problem to fix, not to deploy
  # over. Check before even the health-only path. Include untracked,
  # non-ignored files: they can enter a Docker build context even though
  # `git diff` cannot see them.
  if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "checkout has local changes; refusing to deploy" >&2
    git status --short --untracked-files=all >&2
    exit 1
  fi

  # Last commit that passed the post-deploy health probe (or a no-rebuild
  # fast-forward). Missing means "never recorded a success", so the next
  # tick rebuilds; deleting the ref by hand forces a full redeploy.
  deployed=$(git rev-parse -q --verify "$DEPLOYED_REF" 2>/dev/null || true)
  new=$(git rev-parse origin/main)
  if [ -n "$deployed" ] && [ "$deployed" = "$new" ]; then
    # Nothing to deploy. Probe anyway, so a stack that an earlier run left
    # broken keeps this unit red on every tick instead of turning green as
    # soon as the tip stops moving.
    if ! healthy; then
      echo "nothing to deploy, but the stack does not answer /api/health" >&2
      exit 1
    fi
    exit 0
  fi

  if [ -n "$deployed" ]; then
    changed=$(git diff --name-only "$deployed" "$new")
  else
    # Empty tree: every path counts as changed so a missing ref rebuilds both
    # images (first success after install, or after `git update-ref -d`).
    changed=$(git diff --name-only "$(git hash-object -t tree /dev/null)" "$new")
  fi
  services=()
  grep -qE '^(Dockerfile|requirements/|policy_assistant/)' <<<"$changed" && services+=(api)
  grep -qE '^web/' <<<"$changed" && services+=(web)
  # docker-compose.yml is not a build input; `up` recreates whatever it
  # changed, Caddy included. Both images are rebuilt anyway in case a build
  # arg or context moved; with a warm cache that costs seconds.
  recreate_caddy=
  grep -qE '^docker-compose\.yml$' <<<"$changed" && { services=(api web); recreate_caddy=1; }
  # Caddyfile is bind-mounted, so `up` sees no change and would leave the
  # old config running. Caddy reloads in place, keeping its certificate and
  # its listeners.
  reload_caddy=
  grep -qE '^Caddyfile$' <<<"$changed" && reload_caddy=1

  # Deleting the deployed ref is the documented force-redeploy operation.
  # It must also clear a retry cap for the same commit.
  if [ -z "$deployed" ]; then
    rm -f "$FAILURE_STATE_FILE"
  fi

  failures=$(failure_count_for "$new")
  if [ "$MAX_FAILURES" -gt 0 ] && [ "$failures" -ge "$MAX_FAILURES" ]; then
    echo "skipping rebuild of ${new:0:7}: already failed ${failures} times (AUTO_DEPLOY_MAX_FAILURES=${MAX_FAILURES})" >&2
    exit 1
  fi

  git merge --ff-only --quiet origin/main
  from=${deployed:-none}
  if [ "$from" != none ]; then
    from=${from:0:7}
  fi
  echo "deploy ${from} -> ${new:0:7}: ${services[*]:-nothing to rebuild}${reload_caddy:+, reload caddy}"
  if [ ${#services[@]} -eq 0 ] && [ -z "$reload_caddy" ]; then
    if ! healthy; then
      echo "no rebuild required, but the stack does not answer /api/health" >&2
      exit 1
    fi
    mark_deployed "$new"
    exit 0
  fi

  if [ ${#services[@]} -gt 0 ]; then
    up=("${services[@]}")
    [ -n "$recreate_caddy" ] && up+=(caddy)
    # A full rebuild needs ~1 GiB free; the original 6.8 GB root volume sat
    # near that after one warm cache (issue #79). The volume is 16 GB since
    # 2026-09-05, so this guard is a backstop: prune before build when tight
    # so the unit does not go red until someone prunes by hand. The weekly
    # docker-prune.timer keeps the cache from creeping between rebuilds.
    avail_kb=$(df -Pk / | awk 'NR==2 {print $4}')
    if [ "${avail_kb:-0}" -lt 1048576 ]; then
      echo "root has under 1 GiB free (${avail_kb} KiB); pruning Docker build cache"
      docker builder prune -f --keep-storage 300M
    fi
    # --pull refreshes base images so a Dependabot Docker bump takes effect.
    # --no-deps keeps `up` from restarting Caddy when only api or web changed.
    if ! docker compose build --pull "${services[@]}"; then
      bump_failure "$new"
      exit 1
    fi
    if ! docker compose up -d --no-deps "${up[@]}"; then
      bump_failure "$new"
      exit 1
    fi
  fi
  if [ -n "$reload_caddy" ]; then
    if ! docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile; then
      bump_failure "$new"
      exit 1
    fi
  fi

  # Sixty seconds covers a cold Uvicorn start on a small instance and the
  # few seconds Nginx may keep a stale address for a recreated api.
  for _ in $(seq 30); do
    if healthy; then
      docker compose ps
      # Only now is the previous image safe to drop. Until the probe passes,
      # retagging it is the quickest way back.
      docker image prune -f >/dev/null
      mark_deployed "$new"
      exit 0
    fi
    sleep 2
  done
  echo "stack did not answer /api/health after the deploy" >&2
  docker compose logs --tail 30 web api >&2
  bump_failure "$new"
  exit 1
}

main "$@"
