# Running Tablekeeper (stage 1)

Build the image and start the service on port 8080, from this directory:

```sh
docker build -t tablekeeper-stage-1 . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage-1
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and answers `GET /health` with
`{"status": "ok"}` once it is ready. It needs no network access at run time: the Python standard
library and IANA time zone data are inside the image. State is held in memory and is seeded
through `POST /_test/reset`.

Tests (standard library only, they start the service in-process on a free port):

```sh
python3 -m unittest discover -s tests -v
```
