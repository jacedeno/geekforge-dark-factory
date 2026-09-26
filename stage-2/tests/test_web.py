"""WO 2.3: screen routes return HTML and every asset is served from the image."""

import http.client
import re
import unittest

from support import ServiceTest


class WebTest(ServiceTest):
    def fetch(self, path, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.client.port, timeout=10)
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Type"), body

    def test_screen_routes_are_html(self):
        for path in ("/", "/signup", "/login", "/lookup", "/lookup?ref=X"):
            status, ctype, body = self.fetch(path)
            self.assertEqual((status, ctype), (200, "text/html; charset=utf-8"), path)
            self.assertIn(b"<!doctype html>", body.lower())

    def test_assets_are_local(self):
        _, _, html = self.fetch("/")
        refs = re.findall(rb'(?:src|href)="([^"]+)"', html)
        self.assertTrue(refs)
        for ref in refs:
            if ref.startswith(b"#"):
                continue
            self.assertTrue(ref.startswith(b"/assets/"), ref)
            status, ctype, body = self.fetch(ref.decode())
            self.assertEqual(status, 200, ref)
            self.assertTrue(body)
            self.assertNotIn(b"http://", body.replace(b"http://www.w3.org", b""), ref)
            self.assertNotIn(b"https://", body, ref)

    def test_unknown_assets_and_api_errors_stay_json(self):
        for path in ("/assets/nope.js", "/assets/..%2Fservice.py", "/assets/service.py"):
            status, ctype, _ = self.fetch(path)
            self.assertEqual((status, ctype), (404, "application/json; charset=utf-8"), path)
        status, ctype, _ = self.fetch("/", method="POST")
        self.assertEqual((status, ctype), (405, "application/json; charset=utf-8"))


if __name__ == "__main__":
    unittest.main()
