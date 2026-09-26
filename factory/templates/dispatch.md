# Dispatch template

The **only** message the owner sends in a run. Fill it in, post it in the room mentioning the
planner, and send nothing else until the planner's final report: a follow-up is steering, and
posting it again is a rerun. Everything problem-specific lives here, never in a mandate.

---

@planner You are the coordinator. Build every increment below in order, in this repository,
with the builder and the verifier. Nobody will answer questions until your final report:
decide open points yourself and record them.

Repository (shared checkout, absolute path): <repo>
Specifications directory (read-only, absolute path): <dir holding the spec files>

Increments, in order. Each lives in its own top-level directory, is a complete buildable
service on its own, and extends a copy of the previous one:
1. `<dir-1>/` from `<spec file 1>`
2. `<dir-2>/` from `<spec file 2>`, starting from a copy of `<dir-1>/`
...

Check command for every increment (set it with factory-stage; `{n}` is the increment number):
    <command using {repo} {dir} {n} {out}>
It passes only when the increment's own checks and every earlier one pass, and the next
increment's checks do not. The shipped checks are a partial sample of what will be judged:
build to the specification, and cover what they do not.

Contract for every increment directory: <build file, run instructions file, how it is started,
port, health path, no outbound network at run time, resource limits>.

Stop after the last increment and post the final report.
