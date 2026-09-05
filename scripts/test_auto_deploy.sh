#!/bin/bash
# Synthetic checks for scripts/auto_deploy.sh. Builds a throwaway clone with
# stubbed docker/flock, then asserts retry-from-deployed-ref behaviour.
# No containers, no network, no EC2. Run from repo root or via pytest.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT="$ROOT/scripts/auto_deploy.sh"
PASS=0
FAIL=0
WORK=
CHECKOUT=
BUILD_LOG=
LAST_OUT=
LAST_STATUS=0

cleanup() {
  if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

assert_eq() {
  local label=$1 expected=$2 actual=$3
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS + 1))
    echo "ok - $label"
  else
    FAIL=$((FAIL + 1))
    echo "not ok - $label (expected '$expected', got '$actual')" >&2
  fi
}

assert_contains() {
  local label=$1 needle=$2 haystack=$3
  if [[ "$haystack" == *"$needle"* ]]; then
    PASS=$((PASS + 1))
    echo "ok - $label"
  else
    FAIL=$((FAIL + 1))
    echo "not ok - $label (missing '$needle' in: $haystack)" >&2
  fi
}

assert_file() {
  local label=$1 path=$2
  if [ -f "$path" ]; then
    PASS=$((PASS + 1))
    echo "ok - $label"
  else
    FAIL=$((FAIL + 1))
    echo "not ok - $label (missing $path)" >&2
  fi
}

assert_no_file() {
  local label=$1 path=$2
  if [ ! -f "$path" ]; then
    PASS=$((PASS + 1))
    echo "ok - $label"
  else
    FAIL=$((FAIL + 1))
    echo "not ok - $label (unexpected $path)" >&2
  fi
}

assert_no_build() {
  local label=$1
  if grep -q 'compose build' "$BUILD_LOG"; then
    FAIL=$((FAIL + 1))
    echo "not ok - $label" >&2
  else
    PASS=$((PASS + 1))
    echo "ok - $label"
  fi
}

assert_not_eq() {
  local label=$1 unexpected=$2 actual=$3
  if [ "$unexpected" != "$actual" ]; then
    PASS=$((PASS + 1))
    echo "ok - $label"
  else
    FAIL=$((FAIL + 1))
    echo "not ok - $label (unexpected '$unexpected')" >&2
  fi
}

# One disposable upstream + clone. Stubs live on PATH ahead of real docker so
# auto_deploy never talks to a daemon.
setup_repo() {
  WORK=$(mktemp -d)
  mkdir -p "$WORK/bin" "$WORK/upstream"

  # auto_deploy only needs `command -v flock` and `flock -n 9` to succeed.
  cat >"$WORK/bin/flock" <<'EOF'
#!/bin/bash
exit 0
EOF
  chmod +x "$WORK/bin/flock"

  cat >"$WORK/bin/docker" <<'EOF'
#!/bin/bash
set -euo pipefail
LOG=${BUILD_LOG:?}
echo "docker $*" >>"$LOG"
case "${1:-}" in
  compose)
    shift
    case "${1:-}" in
      build)
        if [ "${DOCKER_MODE:-ok}" = "fail-build" ]; then
          echo "simulated build failure" >&2
          exit 1
        fi
        exit 0
        ;;
      up|ps|logs)
        exit 0
        ;;
      exec)
        if [ "${DOCKER_MODE:-ok}" = "unhealthy" ]; then
          exit 1
        fi
        exit 0
        ;;
      *)
        exit 0
        ;;
    esac
    ;;
  image)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
  chmod +x "$WORK/bin/docker"

  git -C "$WORK/upstream" init -q -b main
  git -C "$WORK/upstream" config user.name test
  git -C "$WORK/upstream" config user.email test@example.com
  mkdir -p "$WORK/upstream/policy_assistant" "$WORK/upstream/web" "$WORK/upstream/scripts" \
    "$WORK/upstream/requirements"
  echo 'api' >"$WORK/upstream/policy_assistant/app.py"
  echo 'web' >"$WORK/upstream/web/index.html"
  echo 'req' >"$WORK/upstream/requirements/base.txt"
  echo 'FROM scratch' >"$WORK/upstream/Dockerfile"
  echo 'services: {}' >"$WORK/upstream/docker-compose.yml"
  echo '# caddy' >"$WORK/upstream/Caddyfile"
  cp "$SCRIPT" "$WORK/upstream/scripts/auto_deploy.sh"
  chmod +x "$WORK/upstream/scripts/auto_deploy.sh"
  git -C "$WORK/upstream" add .
  git -C "$WORK/upstream" commit -q -m 'initial'

  git clone -q "$WORK/upstream" "$WORK/checkout"
  git -C "$WORK/checkout" config user.name test
  git -C "$WORK/checkout" config user.email test@example.com
  export PATH="$WORK/bin:$PATH"
  BUILD_LOG="$WORK/docker.log"
  export BUILD_LOG
  : >"$BUILD_LOG"
  CHECKOUT="$WORK/checkout"
}

commit_upstream() {
  local msg=$1
  shift
  local token path
  for token in "$@"; do
    case "$token" in
      --readme)
        echo "doc $(date +%s%N)" >>"$WORK/upstream/README.md"
        ;;
      --api)
        echo "api $(date +%s%N)" >>"$WORK/upstream/policy_assistant/app.py"
        ;;
      --web)
        echo "web $(date +%s%N)" >>"$WORK/upstream/web/index.html"
        ;;
      --caddy)
        echo "caddy $(date +%s%N)" >>"$WORK/upstream/Caddyfile"
        ;;
      *)
        echo "unknown commit token: $token" >&2
        exit 2
        ;;
    esac
  done
  git -C "$WORK/upstream" add -A
  git -C "$WORK/upstream" commit -q -m "$msg"
}

run_deploy() {
  local status=0
  local out
  out=$(
    cd "$CHECKOUT"
    DOCKER_MODE="${DOCKER_MODE:-ok}" AUTO_DEPLOY_MAX_FAILURES="${AUTO_DEPLOY_MAX_FAILURES:-5}" \
      ./scripts/auto_deploy.sh 2>&1
  ) || status=$?
  LAST_OUT=$out
  LAST_STATUS=$status
}

deployed_sha() {
  git -C "$CHECKOUT" rev-parse -q --verify refs/deployed/main 2>/dev/null || true
}

origin_sha() {
  git -C "$CHECKOUT" fetch -q origin main
  git -C "$CHECKOUT" rev-parse origin/main
}

echo "# auto_deploy synthetic tests"

setup_repo
DOCKER_MODE=ok run_deploy
assert_eq "first success exits 0" 0 "$LAST_STATUS"
assert_eq "first success records deployed ref" "$(origin_sha)" "$(deployed_sha)"
assert_contains "first success rebuilt api" "compose build --pull api" "$(cat "$BUILD_LOG")"
assert_no_file "first success clears failure state" "$CHECKOUT/.git/auto-deploy-failures"

: >"$BUILD_LOG"
DOCKER_MODE=ok run_deploy
assert_eq "second tick nothing to deploy" 0 "$LAST_STATUS"
assert_eq "second tick leaves deployed ref" "$(origin_sha)" "$(deployed_sha)"
assert_no_build "second tick must not rebuild"

# Failed build leaves HEAD forward but deployed behind, so the next tick retries.
commit_upstream "api bump" --api
: >"$BUILD_LOG"
DOCKER_MODE=fail-build run_deploy
assert_eq "failed build exits 1" 1 "$LAST_STATUS"
assert_file "failed build records failure state" "$CHECKOUT/.git/auto-deploy-failures"
old_deployed=$(deployed_sha)
head_now=$(git -C "$CHECKOUT" rev-parse HEAD)
new_tip=$(origin_sha)
assert_eq "failed build fast-forwards HEAD" "$new_tip" "$head_now"
if [ "$old_deployed" = "$new_tip" ]; then
  FAIL=$((FAIL + 1))
  echo "not ok - failed build must not advance deployed ref" >&2
else
  PASS=$((PASS + 1))
  echo "ok - failed build must not advance deployed ref"
fi

: >"$BUILD_LOG"
DOCKER_MODE=ok run_deploy
assert_eq "retry after failure exits 0" 0 "$LAST_STATUS"
assert_eq "retry records deployed ref at tip" "$(origin_sha)" "$(deployed_sha)"
assert_contains "retry rebuilds after failure" "compose build --pull api" "$(cat "$BUILD_LOG")"
assert_no_file "retry clears failure state" "$CHECKOUT/.git/auto-deploy-failures"

# Cap consecutive failures: after N fails, skip rebuild but stay red.
commit_upstream "api again" --api
AUTO_DEPLOY_MAX_FAILURES=2
export AUTO_DEPLOY_MAX_FAILURES
: >"$BUILD_LOG"
DOCKER_MODE=fail-build run_deploy
assert_eq "cap fail 1 exits 1" 1 "$LAST_STATUS"
: >"$BUILD_LOG"
DOCKER_MODE=fail-build run_deploy
assert_eq "cap fail 2 exits 1" 1 "$LAST_STATUS"
: >"$BUILD_LOG"
DOCKER_MODE=fail-build run_deploy
assert_eq "cap skip exits 1" 1 "$LAST_STATUS"
assert_contains "cap skip logs skip message" "skipping rebuild" "$LAST_OUT"
assert_no_build "capped tick must not rebuild"

# Deleting the deployed ref is a force-redeploy even when this SHA hit the cap.
git -C "$CHECKOUT" update-ref -d refs/deployed/main
: >"$BUILD_LOG"
DOCKER_MODE=ok run_deploy
assert_eq "forced redeploy after cap exits 0" 0 "$LAST_STATUS"
assert_eq "forced redeploy records current tip" "$(origin_sha)" "$(deployed_sha)"
assert_contains "forced redeploy after cap rebuilds" "compose build --pull api web" "$(cat "$BUILD_LOG")"

# A newer commit resets the cap and deploys.
commit_upstream "api fix" --api
: >"$BUILD_LOG"
DOCKER_MODE=ok AUTO_DEPLOY_MAX_FAILURES=2 run_deploy
assert_eq "new commit after cap deploys" 0 "$LAST_STATUS"
assert_eq "new commit advances deployed ref" "$(origin_sha)" "$(deployed_sha)"
assert_contains "new commit rebuilds" "compose build --pull api" "$(cat "$BUILD_LOG")"
unset AUTO_DEPLOY_MAX_FAILURES

# Docs-only: advance deployed ref without building images.
commit_upstream "docs only" --readme
: >"$BUILD_LOG"
DOCKER_MODE=ok run_deploy
assert_eq "docs-only exits 0" 0 "$LAST_STATUS"
assert_eq "docs-only advances deployed ref" "$(origin_sha)" "$(deployed_sha)"
assert_no_build "docs-only must not rebuild"

# A docs-only fast-forward must not be marked deployed over an unhealthy stack.
commit_upstream "docs while unhealthy" --readme
before_unhealthy=$(deployed_sha)
: >"$BUILD_LOG"
DOCKER_MODE=unhealthy run_deploy
assert_eq "unhealthy docs-only exits 1" 1 "$LAST_STATUS"
assert_eq "unhealthy docs-only leaves deployed ref" "$before_unhealthy" "$(deployed_sha)"
assert_no_build "unhealthy docs-only must not rebuild"
DOCKER_MODE=ok run_deploy
assert_eq "healthy retry of docs-only exits 0" 0 "$LAST_STATUS"
assert_eq "healthy retry advances deployed ref" "$(origin_sha)" "$(deployed_sha)"

# Untracked files are unreviewed build inputs. An override must be refused and
# COMPOSE_FILE is pinned so Compose cannot auto-load it.
echo 'services: {}' >"$CHECKOUT/docker-compose.override.yml"
: >"$BUILD_LOG"
DOCKER_MODE=ok run_deploy
assert_eq "untracked override exits 1" 1 "$LAST_STATUS"
assert_contains "untracked override reports dirty checkout" "checkout has local changes" "$LAST_OUT"
assert_no_build "untracked override must not build"
rm -f "$CHECKOUT/docker-compose.override.yml"

# Deleting the deployed ref forces a full redeploy of api and web.
git -C "$CHECKOUT" update-ref -d refs/deployed/main
: >"$BUILD_LOG"
DOCKER_MODE=ok run_deploy
assert_eq "reset ref exits 0" 0 "$LAST_STATUS"
assert_eq "reset ref re-records deployed" "$(origin_sha)" "$(deployed_sha)"
assert_contains "reset ref rebuilds api" "compose build --pull api" "$(cat "$BUILD_LOG")"
assert_contains "reset ref rebuilds web" "compose build --pull api web" "$(cat "$BUILD_LOG")"

echo
echo "passed=$PASS failed=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
