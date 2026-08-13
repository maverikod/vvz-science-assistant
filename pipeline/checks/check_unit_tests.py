"""Server unit-test suite as a named pipeline check.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Runs ``pytest tests/`` with the active interpreter and reports the captured
output on failure. Registration is unconditional.
"""

from __future__ import annotations

import subprocess
import sys

from pipeline import registry
from pipeline.live.client import repository_root
from pipeline.registry import CheckResult

CHECK_NAME = "unit-tests"
CHECK_DESCRIPTION = "Run the server unit-test suite (pytest tests/)."


def check_unit_tests() -> CheckResult:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=repository_root(), capture_output=True, text=True,
    )
    output = (completed.stdout + completed.stderr).strip()
    tail = "\n".join(output.splitlines()[-25:])
    if completed.returncode != 0:
        return CheckResult.fail(message=f"pytest tests exited {completed.returncode}", output=output)
    return CheckResult.ok(message=tail.splitlines()[-1] if tail else "pytest tests passed")


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_unit_tests)
