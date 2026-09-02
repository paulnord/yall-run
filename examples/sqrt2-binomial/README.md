# Binomial-series square root of 2

This example computes $\sqrt{2}$ from the generalized binomial expansion

$$
(1+x)^{1/2}=\sum_{n=0}^{\infty}\binom{1/2}{n}x^n.
$$

At $x=1$ this becomes

$$
\sqrt{2}=\sum_{n=0}^{\infty}\binom{1/2}{n}
=1+\frac12-\frac18+\frac1{16}-\frac5{128}+\cdots.
$$

The workflow divides 40,000 terms into eight independent 5,000-term chunks. The chunk tasks fan out in parallel, `sum` combines all eight partial sums, and `check` compares the result with Python's `math.sqrt(2)`.

```text
prepare
   |
   +-- partial-000 --+
   +-- partial-001 --+
   +-- partial-002 --+
   +-- partial-003 --+--> sum --> check
   +-- partial-004 --+
   +-- partial-005 --+
   +-- partial-006 --+
   +-- partial-007 --+
```

This deliberately contrasts with [`../sqrt2`](../sqrt2/), which computes the same constant with a continued fraction whose tasks form a long serial dependency chain.

## Numerical character

The binomial expansion is elegant, but $x=1$ lies on the boundary of its radius of convergence. The series does converge there, and after the first term its signs alternate, but convergence is only algebraic rather than spectacularly fast. Forty thousand terms give an error of roughly $2\times10^{-8}$ in ordinary double precision.

So this is another case where the mathematics is useful for illustrating a workflow shape rather than recommending a state-of-the-art square-root algorithm. Newton's method converges much faster for numerical square roots, while the continued fraction in the companion example also reaches good rational approximations with relatively few steps.

The worker implementation intentionally favors transparency over numerical cleverness: each chunk independently advances the binomial-coefficient recurrence to its own range and then sums only the requested terms. That makes the tasks independent at the cost of some repeated arithmetic.

## Run it

From this directory:

```bash
yawl-run validate
yawl-run plan
yawl-run create -j 8 | yawl-run start
cat sqrt2-binomial-work/sqrt2.txt
cat sqrt2-binomial-work/check.txt
```

For a shared filesystem where small campaign-metadata operations are expensive, node-local campaign storage can make local execution much faster:

```bash
yawl-run create -j 8 --campaigns-dir /tmp/yawl-campaigns | yawl-run start
```

If that storage is temporary, preserve the campaign directory if you need its provenance records later.
