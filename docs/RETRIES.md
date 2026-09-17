# Retry policy

Yall separates failures that happen before the task payload starts from failures reported by the payload itself.

## Payload retries: `%retry`

`%retry N` is the existing task-level retry policy. It requests up to `N` retries after the Yall worker has started the payload and the task fails.

```text
analyze:
    %retry 2
    ./Analyze input.root
```

The default is `%retry 0`: application failures are not retried unless the workflow asks for them.

## Startup retries: `%startup-retry`

`%startup-retry N` controls retries for failures that occur before the Yall payload starts. These include failures in a queued execution wrapper, container launcher, or worker-node environment.

```text
analyze:
    %startup-retry 4
    ./Analyze input.root
```

The default is `%startup-retry 2`, giving a task up to three startup attempts. Use `%startup-retry 0` to disable automatic startup retries.

Startup retries are deliberately separate from `%retry`. A flaky worker or container launch should not spend the retry budget intended for the scientific application, and an application failure should not be mistaken for infrastructure trouble.

## Condor behavior

The Condor backend currently enforces the separate startup retry policy.

Each node has a small payload launcher that creates a marker immediately after the wrapper or container environment has successfully reached the payload. The outer node launcher classifies the result as:

- exit `100`: startup failure before the payload marker;
- exit `101`: the payload started and then failed;
- exit `0`: success.

The original payload exit code remains in Yall's attempt record. The synthetic codes are only used between the node launcher and Condor/DAGMan so the scheduler can distinguish the two failure classes.

For startup retries, Yall renders Condor job-level retry policy:

```text
max_retries = 2
retry_until = ExitCode =!= 100
requirements = (Machine =!= split(LastRemoteHost, "@")[1])
```

Only startup exit `100` is retried by the same Condor job. The `requirements` expression avoids immediately running the retry on the worker that just failed.

Existing `%retry` remains a DAGMan node retry. Yall renders it with `UNLESS-EXIT 100`, so exhausting the startup retry budget does not consume application retries:

```text
RETRY yall_0000_analyze 2 UNLESS-EXIT 100
```

This matches the CERN batch recommendation for transient worker-node failures: retry a failed job while excluding `LastRemoteHost` from the next match.

## Other backends

Local execution has no queued startup layer, so `%startup-retry` has no separate effect there.

Slurm and PBS currently retain their existing `%retry` behavior and do not yet reschedule startup failures separately. The startup-retry value is frozen with the task so those backends can adopt equivalent scheduler-aware behavior later without changing Yallfile syntax.

## Finite defaults

Retries are always bounded. With no retry directives at all:

- payload retries: `0`;
- Condor startup retries: `2`;
- maximum Condor startup attempts before the payload begins: `3`.

This gives transient infrastructure trouble a couple of extra chances without turning a broken campaign into an immortal queue resident.
