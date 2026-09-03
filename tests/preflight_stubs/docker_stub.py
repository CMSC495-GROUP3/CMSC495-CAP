"""PATH-free docker stub for preflight tests. Fixture path is argv[1]."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _emit(line: str = "") -> None:
    """Write LF-terminated output so bash ``read`` does not keep a trailing CR."""
    sys.stdout.buffer.write(f"{line}\n".encode())


def main() -> None:
    """Dispatch the docker subcommands the preflight script calls."""
    fixture_path = Path(sys.argv[1])
    args = sys.argv[2:]
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    networks = {network["id"]: network for network in data.get("networks", [])}

    if args[:2] == ["network", "ls"] and "--quiet" in args:
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
        if fmt == '{{index .Labels "com.docker.compose.project.working_dir"}}':
            _emit(network.get("working_dir", ""))
            return
        if fmt == "{{range .IPAM.Config}}{{println .Subnet}}{{end}}":
            for subnet in network.get("subnets", []):
                _emit(subnet)
            return
        if fmt == '{{index .Options "com.docker.network.bridge.name"}}':
            _emit(network.get("bridge", ""))
            return
        if fmt == "{{.Id}}":
            _emit(network.get("full_id", network_id))
            return
        raise SystemExit(f"unsupported docker inspect format: {fmt!r}")

    raise SystemExit("unsupported docker invocation: " + " ".join(args))


if __name__ == "__main__":
    main()
