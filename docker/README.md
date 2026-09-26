# Clean-container check

`cleanroom.sh <service-dir>` is the factory's stand-in for the graders' build: a service that does
not build and boot from a clean container with no outbound network scores zero, so every delivery
is checked that way before anyone believes it works.

| Step | How | Catches |
|---|---|---|
| Pull base images | Only the `FROM` images of the service's Dockerfile | nothing (graders have them) |
| Offline build | `build --network=none` | dependencies downloaded at build time |
| Offline boot | `run --network=none`, health probed from a sidecar sharing the container's loopback | services that need the network to start, crash on boot, or never listen |
| Serve (`--serve`) | `run -p 127.0.0.1:<free port>:$PORT` | gives acceptance tests a `BASE_URL` |

A service passes only if its dependencies are vendored or come from the standard library.
`factory/selftest/` holds one service that passes and one that must fail.

Runtime: docker when its daemon answers, else podman; force one with `CONTAINER_CLI`.
