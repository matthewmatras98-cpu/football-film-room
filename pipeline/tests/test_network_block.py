"""Child processes inherit the urlopen block."""

from __future__ import annotations

import subprocess
import sys


def test_subprocess_urlopen_is_blocked():
    result = subprocess.run(
        [sys.executable, "-c", "import urllib.request; urllib.request.urlopen('https://example.invalid')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "network blocked" in result.stderr
