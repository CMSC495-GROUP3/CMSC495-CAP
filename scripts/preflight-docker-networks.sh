#!/bin/bash
# Read-only deployment guard for the two fixed proxy-network CIDRs. It checks
# Linux routes and Docker IPAM before deploy.sh stops the running stack.
set -euo pipefail

read_cidr() {
  local key="$1"
  local fallback="$2"
  local value=""

  if [[ -f .env ]]; then
    value="$(sed -n "s/^${key}=//p" .env | tail -n 1 | tr -d '\r')"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
  fi
  printf '%s' "${value:-$fallback}"
}

ipv4_to_int() {
  local address="$1"
  local a b c d octet
  IFS=. read -r a b c d <<<"$address"
  for octet in "$a" "$b" "$c" "$d"; do
    if [[ ! "$octet" =~ ^[0-9]+$ ]] || ((octet < 0 || octet > 255)); then
      printf 'ERROR: invalid IPv4 address %s.\n' "$address" >&2
      return 1
    fi
  done
  printf '%u\n' "$(((a << 24) | (b << 16) | (c << 8) | d))"
}

cidr_bounds() {
  local cidr="$1"
  local address prefix address_int size network
  address="${cidr%/*}"
  if [[ "$cidr" == */* ]]; then
    prefix="${cidr#*/}"
  else
    prefix=32
  fi
  if [[ ! "$prefix" =~ ^[0-9]+$ ]] || ((prefix < 0 || prefix > 32)); then
    printf 'ERROR: invalid IPv4 CIDR %s.\n' "$cidr" >&2
    return 1
  fi
  address_int="$(ipv4_to_int "$address")"
  size=$((1 << (32 - prefix)))
  network=$((address_int & (0xFFFFFFFF ^ (size - 1))))
  printf '%u %u\n' "$network" "$((network + size - 1))"
}

cidrs_overlap() {
  local left_start left_end right_start right_end
  read -r left_start left_end < <(cidr_bounds "$1")
  read -r right_start right_end < <(cidr_bounds "$2")
  ((left_start <= right_end && right_start <= left_end))
}

# Emit "NETWORK_ID BRIDGE_NAME" for Compose networks owned by this checkout
# whose IPAM subnet exactly matches the target. The bridge is taken from the
# Docker option when set; otherwise it is the default br-<12-char-id> name.
owned_subnet_artifacts() {
  local target="$1"
  local network owner subnet full_id bridge

  while IFS= read -r network; do
    [[ -n "$network" ]] || continue
    owner="$(
      docker network inspect \
        --format '{{index .Labels "com.docker.compose.project.working_dir"}}' \
        "$network" 2>/dev/null || true
    )"
    [[ "$owner" == "$(pwd -P)" ]] || continue
    while IFS= read -r subnet; do
      [[ "$subnet" == "$target" ]] || continue
      full_id="$(docker network inspect --format '{{.Id}}' "$network")"
      bridge="$(
        docker network inspect \
          --format '{{index .Options "com.docker.network.bridge.name"}}' \
          "$network"
      )"
      if [[ -z "$bridge" ]]; then
        bridge="br-${full_id:0:12}"
      fi
      printf '%s %s\n' "$network" "$bridge"
    done < <(docker network inspect --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' "$network")
  done < <(docker network ls --quiet)
}

route_entries() {
  # Print "DEST_CIDR DEV" for every non-default IPv4 route. DEV is empty when
  # the route has no device (for example a via-only or blackhole entry).
  ip -4 route show table all | awk '
    function is_cidr(value) {
      return value ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(\/[0-9]+)?$/
    }
    function find_dev(   i) {
      for (i = 1; i <= NF; i++) {
        if ($i == "dev") {
          return $(i + 1)
        }
      }
      return ""
    }
    $1 == "default" { next }
    is_cidr($1) { print $1, find_dev(); next }
    ($1 == "blackhole" || $1 == "broadcast" || $1 == "local" ||
     $1 == "prohibit" || $1 == "throw" || $1 == "unreachable") && is_cidr($2) {
      print $2, find_dev()
    }
  '
}

bridge_is_ignored() {
  local bridge="$1"
  shift
  local candidate
  [[ -n "$bridge" ]] || return 1
  for candidate in "$@"; do
    [[ "$bridge" == "$candidate" ]] && return 0
  done
  return 1
}

network_is_ignored() {
  local network="$1"
  shift
  local candidate
  for candidate in "$@"; do
    [[ "$network" == "$candidate" ]] && return 0
  done
  return 1
}

check_host_routes() {
  local label="$1"
  local target="$2"
  shift 2
  local -a ignore_bridges=("$@")
  local route bridge

  while read -r route bridge; do
    [[ -n "$route" ]] || continue
    if cidrs_overlap "$target" "$route"; then
      if bridge_is_ignored "$bridge" "${ignore_bridges[@]+"${ignore_bridges[@]}"}"; then
        continue
      fi
      printf 'ERROR: %s subnet %s overlaps host route %s.\n' "$label" "$target" "$route" >&2
      return 1
    fi
  done < <(route_entries)
}

check_docker_networks() {
  local label="$1"
  local target="$2"
  shift 2
  local -a ignore_networks=("$@")
  local network subnet

  while IFS= read -r network; do
    [[ -n "$network" ]] || continue
    if network_is_ignored "$network" "${ignore_networks[@]+"${ignore_networks[@]}"}"; then
      continue
    fi
    while IFS= read -r subnet; do
      [[ -n "$subnet" ]] || continue
      if cidrs_overlap "$target" "$subnet"; then
        printf 'ERROR: %s subnet %s overlaps Docker network %s (%s).\n' \
          "$label" "$target" "$network" "$subnet" >&2
        return 1
      fi
    done < <(docker network inspect --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' "$network")
  done < <(docker network ls --quiet)
}

check_subnet() {
  local label="$1"
  local target="$2"
  local network bridge
  local -a owned_networks=()
  local -a owned_bridges=()

  # An existing network owned by this checkout is excluded from collision
  # comparisons (and its bridge route is ignored), but unrelated host routes
  # and unrelated Docker networks are still validated. deploy.sh runs this
  # preflight before `docker compose down`, which removes the owned network
  # before Compose recreates it.
  while read -r network bridge; do
    [[ -n "$network" ]] || continue
    owned_networks+=("$network")
    [[ -n "$bridge" ]] && owned_bridges+=("$bridge")
  done < <(owned_subnet_artifacts "$target")

  if ((${#owned_networks[@]} > 0)); then
    printf 'Subnet %s is already owned by this Compose project; excluding owned network from collision checks.\n' \
      "$target"
  fi

  check_host_routes "$label" "$target" "${owned_bridges[@]+"${owned_bridges[@]}"}"
  check_docker_networks "$label" "$target" "${owned_networks[@]+"${owned_networks[@]}"}"
}

EDGE_SUBNET="${EDGE_SUBNET:-$(read_cidr EDGE_SUBNET 10.250.0.0/24)}"
APP_SUBNET="${APP_SUBNET:-$(read_cidr APP_SUBNET 10.251.0.0/24)}"

# Validate syntax before comparisons so malformed configuration fails clearly.
cidr_bounds "$EDGE_SUBNET" >/dev/null
cidr_bounds "$APP_SUBNET" >/dev/null
if cidrs_overlap "$EDGE_SUBNET" "$APP_SUBNET"; then
  printf 'ERROR: EDGE_SUBNET %s overlaps APP_SUBNET %s.\n' "$EDGE_SUBNET" "$APP_SUBNET" >&2
  exit 1
fi

check_subnet edge "$EDGE_SUBNET"
check_subnet app "$APP_SUBNET"
printf 'Proxy network preflight passed: edge=%s app=%s\n' "$EDGE_SUBNET" "$APP_SUBNET"
