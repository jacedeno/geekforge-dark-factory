"""Minimal stdlib HTTP service used only to self-test the factory's clean-container check."""
import http.server
import json
import os


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
