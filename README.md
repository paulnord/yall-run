# YAWL-run

**Yet Another Workflow Layer**  
**Y'all run!**

YAWL-run is a deliberately small campaign runner for reproducible analysis work. It sits *above* a batch system rather than trying to become one.

The core model is:

```text
campaign
  task
    attempt
```

A campaign is the durable scientific record of what you intended to run. A task is one named unit of work. An attempt is one concrete execution of that task.

YAWL-run aims to own:

- campaign identity and manifests
- stable task names
- attempt history and retries
- stdout/stderr capture
- lightweight provenance
- a simple status view
- backend adapters

YAWL-run explicitly does **not** aim to own:

- machine selection
- resource matching
- queue policy
- arbitrary workflow languages
- dependency graph scheduling

Those belong to execution systems such as HTCondor/DAGMan, Slurm, or other workflow engines.

> Naming note: YAWL-run is not the YAWL (Yet Another Workflow Language) workflow system.

## First prototype

This repository starts with a local backend so the campaign model can be exercised without a cluster. An HTCondor backend is the obvious next adapter.

Requires Python 3.11+.

```bash
python -m pip install -e .

yawl-run validate examples/hello.toml
yawl-run plan examples/hello.toml
yawl-run start examples/hello.toml --root ./campaigns
yawl-run status ./campaigns/<campaign-id>
```

A failed task can be retried:

```bash
yawl-run retry ./campaigns/<campaign-id> task-name
```

## Example campaign

```toml
[campaign]
name = "hello-yawl"

[[task]]
name = "left"
command = "python -c 'print(\"left says hello\")'"

[[task]]
name = "right"
command = "python -c 'print(\"right says hello\")'"
```

## Campaign directory

A run creates a durable directory of the form:

```text
campaigns/
  hello-yawl-20260901T220000Z-a1b2c3d4/
    campaign.json
    provenance.json
    tasks/
      left/
        task.json
        attempts/
          001/
            attempt.json
            stdout.log
            stderr.log
      right/
        ...
```

That directory should remain understandable after the live scheduler state is long gone.

## Design rule

If a feature can be described without mentioning LFHCal, HGCROC, a particular run number, ROOT histograms, or detector-specific conventions, it may belong in YAWL-run. Otherwise it belongs in the analysis-specific layer.

## Status

Prototype. Small on purpose.
