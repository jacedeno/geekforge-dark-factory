"""Browser screens: the four screen routes share one HTML shell; scripts and styles are served
from the image, never from the network (stage-2 UI, E1)."""

import os

from .errors import not_found

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Raw:
    """A non-JSON response body."""

    def __init__(self, content_type, data):
        self.content_type = content_type
        self.data = data


def _file(name):
    path = os.path.join(STATIC_DIR, name)
    ext = os.path.splitext(name)[1]
    if ext not in TYPES or not os.path.isfile(path):
        raise not_found("no such asset")
    with open(path, "rb") as f:
        return Raw(TYPES[ext], f.read())


def page(req):
    return 200, _file("index.html")


def asset(req):
    name = req.params[0]
    if "/" in name or "\\" in name or name.startswith("."):
        raise not_found("no such asset")
    return 200, _file(name)
