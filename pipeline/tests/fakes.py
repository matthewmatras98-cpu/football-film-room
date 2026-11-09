"""In-memory stand-in for the D1 HTTP API. Tests never call Cloudflare."""

from __future__ import annotations

import json
import sqlite3


class SqliteD1:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.calls: list[dict] = []

    def __call__(self, method: str, url: str, headers: dict, body: bytes):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        payload = json.loads(body.decode("utf-8"))
        cursor = self.conn.execute(payload["sql"], payload.get("params") or [])
        self.conn.commit()
        if cursor.description is None:
            results: list[dict] = []
        else:
            columns = [item[0] for item in cursor.description]
            results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        raw = json.dumps(
            {
                "success": True,
                "errors": [],
                "result": [{"success": True, "results": results, "meta": {}}],
            }
        ).encode("utf-8")
        return 200, {"content-type": "application/json"}, raw
