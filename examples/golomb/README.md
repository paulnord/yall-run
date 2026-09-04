# 11-mark Golomb ruler branch-and-bound

This example is deliberately more like a small HPC search than a numerical toy. It searches for an optimal **11-mark Golomb ruler**: integer marks

$$
0=a_0<a_1<\cdots<a_{10}=L
$$

for which every pairwise distance

$$
a_j-a_i,\qquad i<j,
$$

is distinct, while minimizing the ruler length $L$.

The known optimum for 11 marks is 72, but the search code does **not** use 72 as a pruning bound or expected answer. It starts with the looser exclusive limit 80 and must discover a ruler and exhaust every branch capable of beating the best ruler it finds.

## Workflow shape

Eight search tasks partition the tree by the first nonzero mark:

```text
                         +--> search-00 --+
                         +--> search-01 --+
                         +--> search-02 --+
prepare -> incumbent --- +--> search-03 --+
                         +--> search-04 --+--> reduce --> verify
                         +--> search-05 --+
                         +--> search-06 --+
                         +--> search-07 --+
```

The search tasks are independent yall tasks, but they cooperate through `golomb-work/incumbent.json`. When one worker finds a ruler shorter than the current incumbent, it installs the new ruler under a file lock and the other workers can tighten their branch-and-bound limit.

The incumbent only moves downward. That makes stale reads safe: a worker that spends extra time searching below an older, larger bound has done *more* work than necessary, never less. Workers refresh the shared bound only every million search nodes so that a high-latency shared filesystem does not turn a tiny JSON file into the dominant workload.

A branch that contains no ruler is a successful search task. Task failure means the search program itself failed, not that its assigned mathematical branch was empty.

## Why the final reduction is a proof step

Finding a length-72 ruler is not by itself proof that 72 is optimal. Each shard records the bound below which its assigned part of the tree was exhausted. The reducer requires:

1. all eight shards completed;
2. all first-mark partitions needed to beat the final incumbent were covered;
3. every shard exhausted a search domain at least as large as the domain below the final best length; and
4. a valid incumbent ruler exists.

Only then does `reduce.py` set `optimality_established=true`. `verify.py` independently checks the final ruler and all $\binom{11}{2}=55$ pairwise distances.

## Run locally

From this directory:

```bash
yall-run create -j 8 | yall-run start
cat golomb-work/report.txt
```

On a host where the current directory is a high-latency shared filesystem, the computation itself may still be fine but yall campaign bookkeeping can be much faster on node-local storage:

```bash
yall-run create -j 8 --campaigns-dir /tmp/$USER-yall | yall-run start
```

For the cleanest local CPU benchmark, copy the whole example to local scratch first. The eight workers intentionally share the incumbent file, so the example's working directory must itself be shared when the workers run on different machines.

## Run with HTCondor

The same Yallfile can be submitted through DAGMan:

```bash
yall-run create --backend condor | yall-run start
```

This assumes the example directory is visible on the execute nodes, matching yall-run's current Condor `should_transfer_files = NO` model.

## Runtime

The solver is intentionally straightforward Python backtracking with distinct-distance pruning, not a state-of-the-art Golomb-ruler code. Eleven marks is large enough to produce meaningful CPU work and uneven branch runtimes while remaining reasonable for an interactive demonstration. Exact runtime depends strongly on CPU speed, concurrency, filesystem behavior, and how early a good shared incumbent is discovered.
