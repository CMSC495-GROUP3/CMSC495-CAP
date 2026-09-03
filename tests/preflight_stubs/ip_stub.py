"""PATH-free ip-route stub for preflight tests. Fixture path is argv[1]."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    """Print fixture route lines the way ``ip -4 route show`` would."""
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for line in data.get("routes", []):
        # LF only: Windows text-mode print() would emit CRLF and break awk/read.
        sys.stdout.buffer.write(f"{line}\n".encode())


if __name__ == "__main__":
    main()
