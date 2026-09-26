# Running Tablekeeper (stage 3)

Build the image and start the service on port 8080, from this directory:

```sh
docker build -t tablekeeper-stage-3 . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage-3
```

Open `http://localhost:8080/` for the booking screens (`/`, `/signup`, `/login`, `/lookup`); the
JSON API is served by the same process. The service listens on `0.0.0.0:$PORT` (default `8080`)
and answers `GET /health` with `{"status": "ok"}` once it is ready. It needs no network access at
run time: the Python standard library, IANA time zone data and every script, stylesheet and icon
are inside the image (fonts are the browser's local system fonts). State is held in memory and is
seeded through `POST /_test/reset`.

Tests start the service in-process on a free port. The API tests need only the standard library;
the browser tests (`tests/test_ui.py`) run when the `playwright` package with Chromium is
installed and are skipped otherwise:

```sh
python3 -m unittest discover -s tests -v
```
