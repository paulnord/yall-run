# yawl-run examples

These examples are small enough to inspect by hand but are chosen to exercise different workflow shapes. The numerical examples are demonstrations of orchestration first and numerical algorithms second; the Golomb ruler and ROOT examples add more realistic CPU-bound and scientific workloads.

| Example | Calculation | Workflow shape | Numerical character |
| --- | --- | --- | --- |
| [`hello`](hello/) | simple messages | two independent tasks followed by a join | minimal syntax example |
| [`pi`](pi/) | Leibniz series for $\pi$ | broad map followed by one fan-in | extremely slow convergence |
| [`sqrt2`](sqrt2/) | continued fraction for $\sqrt{2}$ | deep serial dependency chain | rapid convergence, exact rational convergents |
| [`sqrt2-binomial`](sqrt2-binomial/) | binomial series for $\sqrt{2}$ | parallel chunks, fan-in, numerical check | converges at the boundary of the binomial series |
| [`e`](e/) | $\sum 1/n!$ | parallel leaves and hierarchical reduction | very rapid convergence |
| [`golomb`](golomb/) | optimal 11-mark Golomb ruler | parallel branch-and-bound with shared incumbent | CPU-bound search with uneven branches and cooperative pruning |
| [`mcmc`](mcmc/) | Bayesian Langau fit with RooStats | eight independent chains, combine, diagnose, plot | stochastic ROOT workload with convergence diagnostics |
| [`muon-lifetime`](muon-lifetime/) | stopped-muon lifetime | parallel three-stage run pipelines followed by fan-in | ROOT fits with per-run checks and weighted combination |
| [`z-scan`](z-scan/) | Z mass/width likelihood scan | 49-way parameter sweep followed by reduction | RooFit likelihood grid over a synthetic resonance |
| [`invariant-mass`](invariant-mass/) | $K_S^0\to\pi^+\pi^-$ reconstruction | independent ROOT-file pipelines followed by merge | four-vector event processing and peak fitting |

## pi: useful workflow, poor algorithm

The Leibniz formula

$$
\pi = 4\sum_{n=0}^{\infty}\frac{(-1)^n}{2n+1}
$$

is beautifully simple and trivially divisible into independent chunks. It is therefore a good map-reduce demonstration. It is not a sensible modern way to calculate $\pi$: its truncation error decreases only on the order of $1/N$, so additional digits are painfully expensive.

## sqrt(2): two methods, two very different graphs

From $x^2=2$ one can derive

$$
x=1+\frac{1}{x+1},
$$

and recursively substitute to obtain

$$
\sqrt{2}=1+\cfrac{1}{2+\cfrac{1}{2+\cfrac{1}{2+\ddots}}}.
$$

The convergents approach $\sqrt{2}$ rapidly and can be represented exactly as rational numbers. The downside for workflow parallelism is also the point of the [`sqrt2`](sqrt2/) example: each step depends on the previous step. Newton iteration would generally be a faster numerical square-root algorithm.

The companion [`sqrt2-binomial`](sqrt2-binomial/) example starts instead from

$$
(1+x)^{1/2}=\sum_{n=0}^{\infty}\binom{1/2}{n}x^n
$$

and sets $x=1$. Its terms can be split into independent chunks, so the same constant now produces a broad parallel graph followed by a fan-in. Numerically this is less attractive than the continued fraction because $x=1$ is on the boundary of the binomial series' radius of convergence, so convergence is only algebraic.

## e: a natural reduction tree

The factorial series

$$
e=\sum_{n=0}^{\infty}\frac{1}{n!}
$$

converges extremely rapidly because $n!$ grows so quickly. Its terms can be split into independent partial sums and combined in any grouping, making it a clean example of hierarchical reduction. For the tiny term counts used here, launching workflow tasks costs vastly more than doing the arithmetic; the graph structure is the lesson.

## Golomb ruler: cooperative branch-and-bound

The [`golomb`](golomb/) example searches for an optimal 11-mark ruler with all pairwise distances distinct. Eight CPU-bound search shards explore disjoint parts of the tree while sharing a monotonically decreasing incumbent. A good solution found by one worker can therefore prune work in the others even though the search tasks have no yawl dependency edges between them.

The final reduction is intentionally stronger than merely selecting the shortest ruler found: it verifies that every shard exhausted the portion of its search space required to rule out anything shorter. This makes the example useful for distinguishing a successful empty branch from a failed task and for testing uneven, interacting parallel workloads.

## ROOT scientific workflows

The [`mcmc`](mcmc/) example generates a synthetic Landau-convolved-with-Gaussian spectrum with RooFit and runs eight independent `RooStats::MCMCCalculator` chains. Parallelism comes from statistically independent chains.

The [`muon-lifetime`](muon-lifetime/) example instead demonstrates *parallel pipelines*: independent run files each move through simulation, fitting, and validation before one weighted combination task.

The [`z-scan`](z-scan/) example demonstrates a classic batch-farm *parameter sweep*. Forty-nine RooFit likelihood evaluations have identical dependencies and can run simultaneously before the likelihood surface is assembled.

The [`invariant-mass`](invariant-mass/) example looks more like ordinary HEP production: several ROOT event files are generated and reconstructed independently, then their invariant-mass histograms are merged and fitted.

All four ROOT examples share the MCMC example's `run-pyroot.sh` launcher, so they use local PyROOT when it is available and otherwise fall back to the EIC Apptainer/Singularity environment.

Each example has its own README with run instructions and more detail about why that calculation or search is useful for testing yawl-run.
