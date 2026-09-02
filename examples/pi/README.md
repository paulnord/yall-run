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
yawl-run start --dry-run
```

The last command renders a Condor/DAGMan campaign without submitting it. Submit the exact rendered campaign with:

```bash
yawl-run submit ../../campaigns/<campaign-id>
```

After completion, the combined estimate is in:

```text
pi-work/pi.txt
```

For a local smoke test, override the backend:

```bash
yawl-run start --backend local
cat pi-work/pi.txt
```
