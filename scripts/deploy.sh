#!/bin/bash
# Deploy helper for a self-managed EC2 host running the Compose stack.
#
# Usage:
#   EC2_HOST=ubuntu@sourcebook.duckdns.org SSH_KEY_PATH=~/.ssh/key.pem ./scripts/deploy.sh
#
# --no-cache is deliberate: Docker will happily reuse a stale layer when only
# application source changed, which produces deploys that silently ship old code.
set -euo pipefail

: "${EC2_HOST:?Set EC2_HOST to the SSH target, for example ubuntu@sourcebook.duckdns.org}"
: "${SSH_KEY_PATH:?Set SSH_KEY_PATH to the private-key file}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-CMSC495-CAP}"
BRANCH="${BRANCH:-main}"

ssh -i "$SSH_KEY_PATH" "$EC2_HOST" "
  set -e
  cd \"$REMOTE_APP_DIR\"
  git pull origin \"$BRANCH\"
  docker compose down
  docker compose build --no-cache
  docker compose up -d
  docker compose ps
"
