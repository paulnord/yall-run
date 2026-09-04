# Pi map-reduce example

This example computes the Leibniz series

$$
\pi = 4\sum_{n=0}^{\infty}\frac{(-1)^n}{2n+1}
    = 4\left(1-\frac13+\frac15-\frac17+\cdots\right).
$$

The series is mathematically simple and embarrassingly parallel, which makes it a useful workflow example. It is **not a good practical algorithm for computing pi**. The Leibniz series converges extremely slowly: after $N$ terms, the error is only of order $1/N$. Getting many correct digits therefore requires an absurd number of terms compared with modern algorithms for pi.

That weakness is useful here. The computation is easy to understand, easy to split into independent pieces, and large enough to demonstrate fan-out and fan-in without hiding the workflow behind sophisticated numerical machinery.

The calculation is divided into eight independent chunks. Every worker runs the same `partial_pi.py` program on a different range file. The final `sum` task depends on the whole `partial-{chunk}` family and receives every partial result through `@input.partial`.

From this directory:

```bash
yall-run validate
yall-run plan
yall-run create
```

The Yallfile declares `backend condor`, so `create` freezes the campaign and renders the Condor/DAGMan files without submitting them. It prints the exact campaign directory.

Start that campaign with:

```bash
yall-run start campaigns/<campaign-id>
```

or create and start it directly:

```bash
yall-run create | yall-run start
```

After completion, the combined estimate is in:

```text
pi-work/pi.txt
```

For a local smoke test, create a separate local campaign from the same Yallfile and choose local task concurrency when the campaign is created:

```bash
yall-run create --backend local -j 4 | yall-run start
cat pi-work/pi.txt
```

`-j` is local-only. Condor processor requests are expressed per task with `%cpus` in the Yallfile.

Each task attempt has a top-level directory such as `partial-000_attempt_001/`. Its `provenance.json` exists before the worker starts, and the worker receives its path in `YALL_PROVENANCE`.
