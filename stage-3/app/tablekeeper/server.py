"""HTTP transport: routing, request parsing and JSON responses (spec sections 3 and 5)."""

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlsplit

from . import web
from .errors import ApiError, malformed
from .service import Request, Service

JSON_TYPE = "application/json; charset=utf-8"
MAX_BODY = 32 * 1024 * 1024

# (path pattern, {method: service attribute or function}); None matches one non-empty segment.
ROUTES = [
    (("",), {"GET": web.page}),
    (("signup",), {"GET": web.page}),
    (("login",), {"GET": web.page}),
    (("lookup",), {"GET": web.page}),
    (("assets", None), {"GET": web.asset}),
    (("health",), {"GET": "health"}),
    (("_test", "reset"), {"POST": "reset"}),
    (("_test", "export"), {"GET": "export"}),
    (("_test", "import"), {"POST": "import_"}),
    (("auth", "signup"), {"POST": "signup"}),
    (("auth", "login"), {"POST": "login"}),
    (("restaurants",), {"GET": "restaurants"}),
    (("restaurants", None), {"GET": "restaurant"}),
    (("availability",), {"GET": "availability"}),
    (("reservations",), {"GET": "list_reservations", "POST": "create_reservation"}),
    (("reservations", None), {"GET": "get_reservation", "PATCH": "patch"}),
    (("reservations", None, "cancel"), {"POST": "cancel"}),
    (("reservation-moves",), {"POST": "moves"}),
]


def match(segments):
    for pattern, methods in ROUTES:
        if len(pattern) != len(segments):
            continue
        params = []
        for want, got in zip(pattern, segments):
            if want is None:
                if not got:
                    break
                params.append(got)
            elif want != got:
                break
        else:
            return methods, params
    return None, None


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "tablekeeper"
        sys_version = ""
        timeout = 120

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body):
            if isinstance(body, web.Raw):
                content_type, data = body.content_type, body.data
            else:
                content_type = JSON_TYPE
                data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data and self.command != "HEAD":
                self.wfile.write(data)

        def send_error(self, code, message=None, explain=None):
            # Transport-level failures (bad request line, oversized headers) keep the envelope.
            # They are client errors, so they never surface as 5xx.
            if code >= 500:
                code = 400
            codes = {404: "not_found", 405: "method_not_allowed"}
            err = ApiError(code, codes.get(code, "malformed_request"),
                           message or "request rejected")
            self.close_connection = True
            try:
                self._send(code, err.body())
            except OSError:
                pass

        def _read_body(self):
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                chunks, total = [], 0
                while True:
                    line = self.rfile.readline(65537)
                    size = int(line.split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline(65537) not in (b"\r\n", b"\n", b""):
                            pass
                        return b"".join(chunks)
                    total += size
                    if total > MAX_BODY:
                        raise malformed("request body too large")
                    chunks.append(self.rfile.read(size))
                    self.rfile.readline(65537)
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > MAX_BODY:
                raise malformed("invalid Content-Length")
            return self.rfile.read(length) if length else b""

        def _dispatch(self):
            try:
                body = self._read_body()
            except (ValueError, ApiError):
                self.close_connection = True
                self._send(400, malformed("unreadable request body").body())
                return
            try:
                url = urlsplit(self.path)
                segments = [unquote(s) for s in url.path.split("/")[1:]]
                methods, params = match(segments)
                if methods is None:
                    raise ApiError(404, "not_found", "no such route")
                name = methods.get(self.command)
                if name is None:
                    raise ApiError(405, "method_not_allowed", "method not allowed on this route")
                query = {}
                for k, v in parse_qsl(url.query, keep_blank_values=True):
                    query.setdefault(k, v)
                req = Request(self.command, url.path, params, query, self.headers, body)
                handler = name if callable(name) else getattr(service, name)
                status, payload = handler(req)
            except ApiError as e:
                status, payload = e.status, e.body()
            except Exception:
                traceback.print_exc(file=sys.stderr)
                status = 500
                payload = ApiError(500, "internal_error", "internal error").body()
            self._send(status, payload)

        def __getattr__(self, name):
            # Every method, including ones http.server has no handler for (TRACE, CONNECT,
            # custom verbs), goes through routing so a known route answers 405, never 501.
            if name.startswith("do_"):
                return self._dispatch
            raise AttributeError(name)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256
    allow_reuse_address = True


def serve(port, host="0.0.0.0", service=None):
    return Server((host, port), make_handler(service or Service()))


def main():
    port = int(os.environ.get("PORT") or 8080)
    httpd = serve(port)
    print(f"tablekeeper listening on 0.0.0.0:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
