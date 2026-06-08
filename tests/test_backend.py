from __future__ import annotations

import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import quote

from backend.server import VibePullHandler


class BackendApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), VibePullHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=120)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read().decode("utf-8")
        connection.close()
        return response.status, json.loads(data)

    def test_health(self) -> None:
        status, payload = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_search_shape(self) -> None:
        status, payload = self.request("GET", f"/api/search?query={quote('animated glowing card')}")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(payload["results"]), 1)
        result = payload["results"][0]
        for key in ("id", "name", "server", "component_type", "faiss_score", "rerank_score"):
            self.assertIn(key, result)

    def test_component(self) -> None:
        status, payload = self.request("GET", "/api/component?id=magic-card")
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "magic-card")

    def test_eval(self) -> None:
        status, payload = self.request("GET", "/api/eval")
        self.assertEqual(status, 200)
        self.assertIn("metrics", payload)

    def test_saved_queries(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/saved-queries",
            {"query_text": "unit test query", "filters": {"source": "all"}},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["query_text"], "unit test query")
        status, payload = self.request("GET", "/api/saved-queries")
        self.assertEqual(status, 200)
        self.assertTrue(
            any(item["query_text"] == "unit test query" for item in payload["saved_queries"])
        )

    def test_compare_boards(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/compare-boards",
            {"name": "unit test board", "component_ids": ["magic-card", "border-beam-demo"]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["name"], "unit test board")
        status, payload = self.request("GET", "/api/compare-boards")
        self.assertEqual(status, 200)
        self.assertTrue(
            any(item["name"] == "unit test board" for item in payload["compare_boards"])
        )


if __name__ == "__main__":
    unittest.main()
