"""Block HTTP in child processes when FOOTBALL_BLOCK_NETWORK=1.

Python imports this module from PYTHONPATH at startup. The pytest fixture
puts tests/ on that path, so CLI subprocesses fail closed.
"""

from __future__ import annotations

import os

if os.environ.get("FOOTBALL_BLOCK_NETWORK") == "1":
    import urllib.request

    def _blocked_urlopen(*args, **kwargs):
        raise RuntimeError("network blocked by football tests")

    urllib.request.urlopen = _blocked_urlopen

    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:

        def _blocked_request(self, *args, **kwargs):
            raise RuntimeError("network blocked by football tests")

        requests.sessions.Session.request = _blocked_request
