#!/usr/bin/env bash
# Scan both dependency trees for known vulnerabilities. `make audit` and the
# Security workflow run this same script, so an exception lives in one place.
#
# Usage: ./scripts/audit.sh            (from the repo root, with .venv set up)
set -euo pipefail

cd "$(dirname "$0")/.."

# Advisories accepted for now. Each entry needs a reason and a way out.
IGNORED_ADVISORIES=(
  # ecdsa < 0.19.3 is a transitive dependency of python-jose. The advisory is a
  # timing side channel in signing; the app only verifies JWTs it signed with
  # HS256, which never touches ecdsa. Upstream has stated they will not fix it.
  # Way out: replace python-jose with PyJWT, which does not depend on ecdsa.
  PYSEC-2026-1325
)

ignore_flags=()
for advisory in "${IGNORED_ADVISORIES[@]}"; do
  ignore_flags+=(--ignore-vuln "$advisory")
done

pip_audit="${PIP_AUDIT:-.venv/bin/pip-audit}"
if [[ ! -x "$pip_audit" ]]; then
  pip_audit="pip-audit"
fi

echo "== Python"
"$pip_audit" \
  -r requirements.txt -r backend/requirements.txt -r requirements-dev.txt \
  --progress-spinner off "${ignore_flags[@]}"

echo "== npm"
(cd frontend && npm audit --audit-level=high)
