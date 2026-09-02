# e reduction-tree example

This example evaluates the familiar series

$$
e = \sum_{n=0}^{\infty}\frac{1}{n!}
  = 1 + 1 + \frac{1}{2!} + \frac{1}{3!} + \cdots.
$$

The campaign uses the first 32 terms. Eight independent tasks each compute four terms exactly as a rational number. Those partial sums are then combined through a balanced reduction tree:

```text
terms-00 --\
terms-01 --- pair-0 --\
terms-02 --\          \
terms-03 --- pair-1 --- group-0 --\
                                   \
terms-04 --\                        sum -> check
terms-05 --- pair-2 --- group-1 --/
terms-06 --\          /
terms-07 --- pair-3 --/
```

The arithmetic remains exact through the entire tree using Python `Fraction`. Only the final `check` task converts the result to floating point.

## Why this is a useful yawl example

The pi example has one broad fan-out followed by one fan-in. This example exercises a more realistic multi-stage sub-analysis: independent leaves, several intermediate reductions, and a final dependent result. With local `-j` greater than one, independent branches can proceed simultaneously while every reduction still waits for its own parents.

It is also small enough that `yawl-run plan` makes the graph structure easy to inspect.

## Numerical character

The factorial series is a genuinely good way to evaluate $e$. The terms decrease extremely rapidly because $n!$ grows so quickly. The remainder after truncating at $N$ terms is tiny, and only a modest number of terms is needed for ordinary floating-point precision.

For this toy problem the workflow overhead is vastly larger than the arithmetic itself. No sensible program would launch a batch job merely to add four reciprocals of factorials. The point is the reduction topology, not computational efficiency.

There are also faster or more specialized arbitrary-precision algorithms for enormous numbers of digits. Here, the factorial series has the useful combination of mathematical clarity, rapid convergence, and naturally separable partial sums.

## Run it

From this directory:

```bash
yawl-run validate
yawl-run plan
yawl-run create -j 4 | yawl-run start
cat e-work/e.txt
```

You can also render the same frozen graph for a queue backend, for example:

```bash
yawl-run create --backend condor
```
