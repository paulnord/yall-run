# Portable resource requests

`%cpus`, `%memory`, `%disk`, and `%time` describe requested resources for a task.
An unindented directive supplies a campaign default; an indented directive
supplies an override for that task, including every member of a task family.

```text
campaign walltime-example
backend condor
%cpus 1
%memory 4GB
%disk 8GB
%time 2h

prepare:
    %time 5m
    echo prepare

analyze: prepare
    echo analyze
```

Here `prepare` requests five minutes and `analyze` requests two hours.

## Wall time: `%time`

Wall time is elapsed job runtime, not CPU time and not time waiting in the queue.
`%time` requests a runtime budget for each scheduled task job, not the sum for
the campaign. Include container startup and other job setup in the requested
budget. Enforcement depends on the backend and site policy, which may impose a
lower queue/partition maximum. In particular, the Condor mapping below requires
supporting site policy; it is not a universally enforced timeout.

Accepted finite durations are:

| Input | Meaning |
| --- | --- |
| `7200` or `7200s` | 7,200 seconds |
| `120m` or `2h` | Two hours |
| `1h30m` | One hour and thirty minutes |
| `2d3h4m5s` | Two days, three hours, four minutes, five seconds |
| `02:00:00` | Hours, minutes, seconds |
| `1-01:00:00` or `25:00:00` | Twenty-five hours |

Unit letters are case-insensitive and must appear once each in descending
`d`, `h`, `m`, `s` order; components may be omitted. Use integers, not fractional
units, and do not put spaces between components. In clock notation minutes
and seconds must each be two digits from `00` to `59`; when days are supplied,
the hour field must be below 24. Bare integers **always mean seconds**, even on
Slurm. Ambiguous two-field `MM:SS`, zero, negative, fractional, unlimited, and
out-of-range values are rejected with the Yallfile line number. The portable
model accepts 1 through 2,147,483,647 seconds; site limits may be much lower.

Omitting `%time` emits **no time directive** and preserves the scheduler's
existing default. Omission does not mean unlimited runtime. There is no new
yall default of 20 minutes, two hours, or any other duration.

## Scheduler mappings

For `%time 2h`:

| Backend | Generated request |
| --- | --- |
| HTCondor (requires site support for `MaxRuntime`) | `+MaxRuntime = 7200` in the task submit description |
| Slurm | `#SBATCH --time=02:00:00` |
| PBS | `#PBS -l walltime=02:00:00` |
| Local | Record the request in provenance; no timeout is enforced |

### HTCondor: site runtime policy versus execution timeout

**The Condor `%time` mapping sets `MaxRuntime`. It does not set HTCondor's
separate `allowed_execute_duration` timeout. These are not aliases.**

The leading `+` is standard HTCondor syntax for adding a job ClassAd attribute.
The meaning of `MaxRuntime`, however, is **site-defined**, not built into
HTCondor as a universal runtime request. CERN and FZU both document this
convention, so it is not CERN-only. Other pools may use a different attribute;
DESY NAF, for example, documents `+RequestRuntime`. Yall currently emits
`+MaxRuntime` for Condor and does not automatically detect or substitute a
site's alternative. Check the target pool's documentation before relying on it.

Without policy that reads `MaxRuntime`, the attribute is just stored in the job
ClassAd: an unfamiliar custom attribute does not itself cause an unsupported
option error, but it neither grants the requested runtime nor enforces a
timeout. The pool's other limits still apply. At supporting sites such as CERN
and FZU, site policy also enforces the requested maximum. Thus the distinction
is **site-defined policy versus a built-in timeout**, not simply "request
versus enforcement."

| Submit setting | Interpretation | Set by Yall's Condor `%time` mapping? |
| --- | --- | --- |
| `+MaxRuntime = 7200` | Site-defined runtime request/limit, in seconds; scheduling and enforcement depend on the pool. | Yes |
| `allowed_execute_duration = 7200` | HTCondor's built-in execution-duration limit, in seconds; exceeding it puts the job on hold (hold code 47). | No |

`allowed_execute_duration` excludes HTCondor file-transfer time. For
self-checkpointing jobs, it limits the execution interval between checkpoints.
Do not assume a site's `MaxRuntime` policy uses identical accounting.

For illustration, a manually configured submit description could contain both:

```text
# Site-defined two-hour runtime request/limit:
+MaxRuntime = 7200
# Independent one-hour HTCondor execution timeout:
allowed_execute_duration = 3600
```

For an ordinary job without self-checkpointing, the second setting can hold it
after one hour even when site policy permits two hours. Conversely, a longer
`allowed_execute_duration` cannot override a shorter site limit. Both policies
apply independently. **Yall does not emit the second line**, add `JobFlavour`,
or install an automatic removal/hold policy as a fallback. A pool may still
supply its own policies independently of Yall.

References: [HTCondor submit-language documentation](https://htcondor.readthedocs.io/en/latest/man-pages/htcondor-jdl.html),
[CERN/LHCb runtime guidance](https://lhcb.github.io/starterkit-lessons/self-guided-lessons/htcondor-more-options.html#resources-and-requirements),
[FZU execution-time policy](https://www.farm.particle.cz/documentation/job-submission-file-examples/htcondor-batch-system/job-restrictions-and-resource-limits/job-execution-time/),
and [DESY NAF submit settings](https://docs.desy.de/naf/documentation/job-submit/).

### Other backends and retries

Slurm accepts seconds in the rendered request but rounds them up to the next
minute. Thus `%time 61s` emits `--time=00:01:01` and Slurm grants a two-minute
limit, subject to site policy. For durations of at least one day, yall uses
`D-HH:MM:SS` for Slurm and total `HH:MM:SS` hours for PBS, without wrapping at
midnight. See [Slurm's `--time` documentation](https://slurm.schedmd.com/sbatch.html#OPT_time)
and the [OpenPBS walltime examples](https://community.openpbs.org/t/jobs-maybe-running-in-one-node-possible-reason-for-getting-killed/3924).

For Condor, DAGMan retries receive new jobs with the same request. Slurm/PBS
currently execute `%retry` attempts inside one batch script, so those attempts
**share** that job's time allocation. This feature does not change retry policy.

The local backend records the request, just as it records other resource
metadata, but does not introduce a subprocess watchdog or kill local jobs.

## Inspecting and preserving the request

```bash
yall-run validate
yall-run plan
yall-run plan --json
```

Text plans show the effective `time=HH:MM:SS` after default/override resolution.
JSON plans, frozen task resources in `campaign.json`, and worker launch
provenance record the requested integer as `walltime_seconds`. This is the
requested value, not a measurement of actual runtime or a rounded scheduler
allocation. Relational exports include `task.walltime_seconds` as a nullable
integer; older campaigns without it continue to load and export as null.

**Editing a Yallfile does not change a campaign already created from it.**
Rendered requests are frozen at `create`. A recovery operation that reuses
those scripts also reuses their time limits. Adding `%time 2h` to the source
and then resuming an old 1,200-second job does not upgrade that old request.
Create a new campaign to use the changed resource policy; choose a fresh
product directory when needed to preserve previous outputs. A future explicit,
audited recovery override would be a separate feature.
