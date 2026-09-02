# Pi map-reduce example

This example computes the Leibniz series

```text
pi = 4 * (1 - 1/3 + 1/5 - 1/7 + ...)
```

in eight independent chunks. Every worker runs the same `partial_pi.py` program on a different range file. The final `sum` task depends on the whole `partial-{chunk}` family and receives every partial result through `@input.partial`.

From this directory:

```bash
yawl-run validate
yawl-run plan
yawl-run create
```

The Yawlfile declares `backend condor`, so `create` freezes the campaign and renders the Condor/DAGMan files without submitting them. It prints the exact campaign directory.

Start that campaign with:

```bash
yawl-run start campaigns/<campaign-id>
```

After completion, the combined estimate is in:

```text
pi-work/pi.txt
```

For a local smoke test, create a separate local campaign from the same Yawlfile and choose local task concurrency when the campaign is created:

```bash
yawl-run create --backend local -j 4
yawl-run start campaigns/<local-campaign-id>
cat pi-work/pi.txt
```

`-j` is local-only. Condor processor requests are expressed per task with `%cpus` in the Yawlfile.

Each task attempt has a top-level directory such as `partial-000_attempt_001/`. Its `provenance.json` exists before the worker starts, and the worker receives its path in `YAWL_PROVENANCE`.
