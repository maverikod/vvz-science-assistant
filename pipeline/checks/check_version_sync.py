"""Release-version coherence across every in-repo version source.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

The single release-version source is the root ``pyproject.toml``. This check
asserts, without building anything, that every derived source agrees with it:
the client ``version.py``, both agent helper scripts, and that the
``mcp-proxy-adapter`` pins are coherent — the client's exact pin must satisfy
the server's range (the release scripts derive wheel names from that pin;
see ``scripts/verify_release.py``).
"""

from __future__ import annotations

import re

from pipeline import registry
from pipeline.live.client import repository_root
from pipeline.registry import CheckResult

CHECK_NAME = "version-sync"
CHECK_DESCRIPTION = (
    "Assert the root pyproject version matches client version.py and both agent "
    "helpers, and that the client's exact mcp-proxy-adapter pin satisfies the "
    "server's requirement floor.")


def check_version_sync() -> CheckResult:
    root = repository_root()
    lines = []
    problems = []

    root_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version\s*=\s*"([^"]+)"', root_text, re.M)
    if not version_match:
        return CheckResult.fail(message="root pyproject.toml has no static project version")
    version = version_match.group(1)
    lines.append(f"root pyproject version: {version}")

    client_text = (root / "client/science_assistant_client/version.py").read_text(encoding="utf-8")
    client_version = re.search(r'__version__\s*=\s*"([^"]+)"', client_text)
    client_version = client_version.group(1) if client_version else None
    if client_version != version:
        problems.append(f"client version.py has {client_version!r}, expected {version!r}")

    for agent_relative in ("scripts/mcp_file_parts.py", "agent/mcp_file_sender.py"):
        agent_text = (root / agent_relative).read_text(encoding="utf-8")
        agent_version = re.search(r'^__version__\s*=\s*"([^"]+)"', agent_text, re.M)
        agent_version = agent_version.group(1) if agent_version else None
        if agent_version != version:
            problems.append(f"{agent_relative} has {agent_version!r}, expected {version!r}")

    pin_match = re.search(r'"mcp-proxy-adapter==([^"]+)"', (root / "client/pyproject.toml").read_text(encoding="utf-8"))
    floor_match = re.search(r'"mcp-proxy-adapter>=([^,"]+)', root_text)
    if not pin_match or not floor_match:
        problems.append("could not read the mcp-proxy-adapter pin (client) or floor (server)")
    else:
        pin, floor = pin_match.group(1), floor_match.group(1)
        lines.append(f"adapter pin (client): {pin}; adapter floor (server): {floor}")
        as_tuple = lambda v: tuple(int(p) for p in v.split("."))  # noqa: E731
        try:
            if as_tuple(pin) < as_tuple(floor):
                problems.append(f"client pin {pin} is below the server floor {floor}")
        except ValueError:
            problems.append(f"unparsable adapter versions: pin={pin!r} floor={floor!r}")

    if problems:
        return CheckResult.fail(message="; ".join(problems), output="\n".join(lines))
    return CheckResult.ok(message=f"all version sources agree on {version}", output="\n".join(lines))


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_version_sync)
