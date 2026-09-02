# yawl-run examples

These examples are small enough to inspect by hand but are chosen to exercise different workflow shapes. The numerical examples are demonstrations of orchestration first and numerical algorithms second.

| Example | Calculation | Workflow shape | Numerical character |
| --- | --- | --- | --- |
| [`hello`](hello/) | simple messages | two independent tasks followed by a join | minimal syntax example |
| [`pi`](pi/) | Leibniz series for $\pi$ | broad map followed by one fan-in | extremely slow convergence |
| [`sqrt2`](sqrt2/) | continued fraction for $\sqrt{2}$ | deep serial dependency chain | rapid convergence, exact rational convergents |
| [`e`](e/) | $\sum 1/n!$ | parallel leaves and hierarchical reduction | very rapid convergence |

## pi: useful workflow, poor algorithm

The Leibniz formula

$$
\pi = 4\sum_{n=0}^{\infty}\frac{(-1)^n}{2n+1}
$$

is beautifully simple and trivially divisible into independent chunks. It is therefore a good map-reduce demonstration. It is not a sensible modern way to calculate $\pi$: its truncation error decreases only on the order of $1/N$, so additional digits are painfully expensive.

## sqrt(2): a naturally serial calculation

From $x^2=2$ one can derive

$$
x=1+\frac{1}{x+1},
$$

and recursively substitute to obtain

$$
\sqrt{2}=1+\cfrac{1}{2+\cfrac{1}{2+\cfrac{1}{2+\ddots}}}.
$$

The convergents approach $\sqrt{2}$ rapidly and can be represented exactly as rational numbers. The downside for workflow parallelism is also the point of the example: each step depends on the previous step. Newton iteration would generally be a faster numerical square-root algorithm.

## e: a natural reduction tree

The factorial series

$$
e=\sum_{n=0}^{\infty}\frac{1}{n!}
$$

converges extremely rapidly because $n!$ grows so quickly. Its terms can be split into independent partial sums and combined in any grouping, making it a clean example of hierarchical reduction. For the tiny term counts used here, launching workflow tasks costs vastly more than doing the arithmetic; the graph structure is the lesson.

Each numerical example has its own README with the derivation, run instructions, and more detail about why that particular calculation is useful for testing yawl-run.
