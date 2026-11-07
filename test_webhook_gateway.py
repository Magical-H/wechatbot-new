import base64
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from webhook_gateway import create_app


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 16).decode()


class _ManagementHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _respond(self):
        self.server.calls.append((self.command, self.path))
        status, body = self.server.responses.get(
            (self.command, self.path), (404, {"error": "not found"})
        )
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _respond
    do_POST = _respond


class WebhookGatewayTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.management = ThreadingHTTPServer(("127.0.0.1", 0), _ManagementHandler)
        self.management.daemon_threads = True
        self.management.calls = []
        self.management.responses = {
            ("GET", "/bot_status"): (200, {"status": "running"}),
            ("POST", "/start_bot"): (202, {"status": "started"}),
            ("POST", "/stop_bot"): (409, {"error": "already stopped"}),
        }
        self.management_thread = threading.Thread(target=self.management.serve_forever, daemon=True)
        self.management_thread.start()
        self.app = create_app(
            "secret",
            lambda *args: self.calls.append(args) or True,
            self.management.server_port,
        )
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer secret"}

    def tearDown(self):
        self.management.shutdown()
        self.management.server_close()
        self.management_thread.join(timeout=2)

    def test_auth_and_text_send(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code, 401)
        response = self.client.post(
            "/api/v1/messages",
            headers=self.auth,
            json={"target": "Alice", "text": "hi"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, [("text", "Alice", "hi")])

    def test_bot_routes_forward_to_management_api(self):
        status = self.client.get("/api/v1/bot/status", headers=self.auth)
        start = self.client.post("/api/v1/bot/start", headers=self.auth)
        stop = self.client.post("/api/v1/bot/stop", headers=self.auth)

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json(), {"status": "running"})
        self.assertEqual(start.status_code, 202)
        self.assertEqual(start.get_json(), {"status": "started"})
        self.assertEqual(stop.status_code, 409)
        self.assertEqual(stop.get_json(), {"error": "already stopped"})
        self.assertTrue(all(isinstance(response.get_json(), dict) for response in (status, start, stop)))
        self.assertEqual(
            self.management.calls,
            [("GET", "/bot_status"), ("POST", "/start_bot"), ("POST", "/stop_bot")],
        )

    def test_base64_image_is_cleaned_after_send(self):
        seen = []

        def sender(action, target, path):
            seen.append((action, target, path, os.path.exists(path)))
            return True

        app = create_app("secret", sender, self.management.server_port)
        response = app.test_client().post(
            "/api/v1/images",
            headers=self.auth,
            json={"target": "Alice", "image_base64": PNG},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, [("image", "Alice", seen[0][2], True)])
        self.assertFalse(os.path.exists(seen[0][2]))

    def test_bad_base64_and_non_image(self):
        invalid = self.client.post(
            "/api/v1/images", headers=self.auth,
            json={"target": "A", "image_base64": "%%%"},
        )
        non_image = self.client.post(
            "/api/v1/images", headers=self.auth,
            json={"target": "A", "image_base64": base64.b64encode(b"not image").decode()},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(non_image.status_code, 415)

    def test_url_private_address_rejected(self):
        with patch(
            "webhook_gateway.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 80))],
        ):
            response = self.client.post(
                "/api/v1/images", headers=self.auth,
                json={"target": "A", "image_url": "http://localhost/x.png"},
            )
        self.assertEqual(response.status_code, 400)

    def test_redirect_to_private_address_rejected(self):
        from webhook_gateway import download_image

        public = [(2, 1, 6, "", ("8.8.8.8", 80))]
        private = [(2, 1, 6, "", ("127.0.0.1", 80))]
        redirect = MagicMock()
        redirect.status = 302
        redirect.headers = {"Location": "http://127.0.0.1/internal"}
        redirect.__enter__.return_value = redirect
        manager = MagicMock()
        with tempfile.TemporaryDirectory() as directory, \
             patch("webhook_gateway.socket.getaddrinfo", side_effect=[public, private]), \
             patch("webhook_gateway.requests.utils.get_environ_proxies", return_value={}), \
             patch("webhook_gateway.requests.utils.select_proxy", return_value=None), \
             patch("webhook_gateway.urllib3.PoolManager", return_value=manager):
            manager.connection_from_host.return_value.urlopen.return_value = redirect
            with self.assertRaises(ValueError):
                download_image("https://public.example/image.png", os.path.join(directory, "img"))
        manager.connection_from_host.assert_called_once()
        manager.connection_from_host.return_value.urlopen.assert_called_once()

    def test_management_api_unavailable(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            unused_port = listener.getsockname()[1]
        app = create_app("secret", lambda *_: True, unused_port)
        response = app.test_client().get("/api/v1/bot/status", headers=self.auth)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"error": "管理端不可用"})

    def test_bot_unavailable(self):
        app = create_app(
            "secret",
            lambda *_: (_ for _ in ()).throw(ConnectionError()),
            self.management.server_port,
        )
        response = app.test_client().post(
            "/api/v1/messages", headers=self.auth, json={"target": "A", "text": "hi"}
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
