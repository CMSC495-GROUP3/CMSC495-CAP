#!/bin/bash
# Deploy now instead of waiting for the auto-deploy timer on the EC2 host.
# Runs the same scripts/auto_deploy.sh the timer runs, then prints its log.
#
# Usage:
#   EC2_HOST=ubuntu@policy-assistant.duckdns.org SSH_KEY_PATH=~/.ssh/key.pem ./scripts/deploy.sh
set -euo pipefail

: "${EC2_HOST:?Set EC2_HOST to the SSH target, for example ubuntu@policy-assistant.duckdns.org}"
: "${SSH_KEY_PATH:?Set SSH_KEY_PATH to the private-key file}"

ssh -i "$SSH_KEY_PATH" "$EC2_HOST" "
  set -e
  sudo systemctl start auto-deploy.service
  sudo journalctl -u auto-deploy.service -n 30 --no-pager
"
