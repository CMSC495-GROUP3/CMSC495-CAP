#!/bin/bash
# Deploy now instead of waiting for the auto-deploy timer on the EC2 host.
# Runs the same scripts/auto_deploy.sh the timer runs, then prints that run's
# journal and exits with the deploy's own status.
#
# Usage:
#   EC2_HOST=ubuntu@sourcebook.duckdns.org SSH_KEY_PATH=~/.ssh/key.pem ./scripts/deploy.sh
set -euo pipefail

: "${EC2_HOST:?Set EC2_HOST to the SSH target, for example ubuntu@sourcebook.duckdns.org}"
: "${SSH_KEY_PATH:?Set SSH_KEY_PATH to the private-key file}"

# Single quotes: everything expands on the instance. No `set -e`, since the
# journal is most useful exactly when the deploy fails. The invocation-ID
# filter prints this run's lines and nothing from earlier runs.
ssh -i "$SSH_KEY_PATH" "$EC2_HOST" '
  sudo systemctl start auto-deploy.service; status=$?
  sudo journalctl -u auto-deploy.service --no-pager \
    _SYSTEMD_INVOCATION_ID="$(systemctl show -p InvocationID --value auto-deploy.service)"
  exit $status
'
