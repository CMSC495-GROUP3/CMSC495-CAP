"""PATH-free docker stub for preflight tests. Fixture path is argv[1]."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _emit(line: str = "") -> None:
    """Write LF-terminated output so bash ``read`` does not keep a trailing CR."""
    sys.stdout.buffer.write(f"{line}\n".encode())


def _label_key(fmt: str) -> str | None:
    prefix = '{{index .Labels "'
    suffix = '"}}'
    if fmt.startswith(prefix) and fmt.endswith(suffix):
        return fmt[len(prefix) : -len(suffix)]
    return None


def _fail_mode(data: dict) -> str | None:
    fail = data.get("fail")
    if isinstance(fail, str) and fail:
        return fail
    return None


def main() -> None:
    """Dispatch the docker subcommands the preflight script calls."""
    fixture_path = Path(sys.argv[1])
    args = sys.argv[2:]
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    networks = {network["id"]: network for network in data.get("networks", [])}
    fail = _fail_mode(data)

    if args[:2] == ["compose", "config"]:
        if fail == "compose_config":
            print("docker compose config failed", file=sys.stderr)
            raise SystemExit(1)
        _emit(f"name: {data.get('project', 'cmsc495-cap-team')}")
        return

    if args[:2] == ["network", "ls"] and "--quiet" in args:
        if fail == "network_ls":
            print("docker network ls failed", file=sys.stderr)
            raise SystemExit(1)
        for network in data.get("networks", []):
            _emit(network["id"])
        return

    if args[:2] == ["network", "inspect"]:
        fmt = None
        targets: list[str] = []
        index = 2
        while index < len(args):
            if args[index] == "--format":
                fmt = args[index + 1]
                index += 2
                continue
            targets.append(args[index])
            index += 1
        if not targets:
            raise SystemExit("missing network id")
        network_id = targets[0]
        network = networks.get(network_id)
        if network is None:
            raise SystemExit(f"network {network_id} not found")

        label_key = _label_key(fmt or "")
        if label_key == "com.docker.compose.project":
            if fail == "inspect_project_label":
                print("project label inspect failed", file=sys.stderr)
                raise SystemExit(1)
            labels = network.get("labels", {})
            _emit(str(labels.get(label_key, "")))
            return
        if label_key == "com.docker.compose.network":
            if fail == "inspect_network_label":
                print("network label inspect failed", file=sys.stderr)
                raise SystemExit(1)
            labels = network.get("labels", {})
            _emit(str(labels.get(label_key, "")))
            return
        if label_key is not None:
            labels = network.get("labels", {})
            _emit(str(labels.get(label_key, "")))
            return
        if fmt == "{{range .IPAM.Config}}{{println .Subnet}}{{end}}":
            if fail == "inspect_subnet":
                print("subnet inspect failed", file=sys.stderr)
                raise SystemExit(1)
            for subnet in network.get("subnets", []):
                _emit(subnet)
            return
        if fmt == '{{index .Options "com.docker.network.bridge.name"}}':
            if fail == "inspect_bridge":
                print("bridge option inspect failed", file=sys.stderr)
                raise SystemExit(1)
            _emit(network.get("bridge", ""))
            return
        if fmt == "{{.Id}}":
            if fail == "inspect_network_id":
                print("network id inspect failed", file=sys.stderr)
                raise SystemExit(1)
            _emit(network.get("full_id", network_id))
            return
        raise SystemExit(f"unsupported docker inspect format: {fmt!r}")

    raise SystemExit("unsupported docker invocation: " + " ".join(args))


if __name__ == "__main__":
    main()
