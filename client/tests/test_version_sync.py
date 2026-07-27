from pathlib import Path
import re
from science_assistant_client import __version__

def test_client_version_matches_server_pyproject() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1) == __version__
