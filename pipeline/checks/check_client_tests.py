"""Client unit-test suite as a named pipeline check.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Runs ``pytest client/tests`` with the active interpreter. Registration is
unconditional; a missing pytest-asyncio or uninstalled client package is a
FAIL with the reason, never a skip.
"""

from __future__ import annotations

import subprocess
import sys

from pipeline import registry
from pipeline.live.client import repository_root
from pipeline.registry import CheckResult

CHECK_NAME = "client-tests"
CHECK_DESCRIPTION = "Run the client unit-test suite (pytest client/tests)."


def check_client_tests() -> CheckResult:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "client/tests", "-q"],
        cwd=repository_root(), capture_output=True, text=True,
    )
    output = (completed.stdout + completed.stderr).strip()
    tail = "\n".join(output.splitlines()[-25:])
    if completed.returncode != 0:
        return CheckResult.fail(message=f"pytest client/tests exited {completed.returncode}", output=output)
    return CheckResult.ok(message=tail.splitlines()[-1] if tail else "pytest client/tests passed")


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_client_tests)
