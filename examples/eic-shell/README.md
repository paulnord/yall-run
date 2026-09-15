# Use eic-shell directly as the wrapper

Requires yall-run 0.9.0 or newer, an installed Linux `eic-shell` launcher, and a configured HTCondor/DAGMan host. This checks ROOT and Python without downloading LFHCal data.

Run from the **host shell**, not from inside the container:

```bash
export EIC_SHELL="$HOME/eic/eic-shell"
test -x "$EIC_SHELL"
cd examples/eic-shell
yall-run validate
yall-run plan
CAMPAIGN=$(yall-run create)
yall-run start "$CAMPAIGN"
yall-run status "$CAMPAIGN"
```

The Yallfile uses the installed launcher directly:

```text
@env EIC_SHELL
%wrapper {EIC_SHELL} --
```

The three tasks check the ROOT and Python versions inside the environment, then report completion. Task stdout is saved in each `<task>_attempt_001/stdout.log`. Launcher startup errors are in the backend's scheduler logs.

Yall archives the launcher at creation and preserves `--` before its bundled worker command. It does not require yall-run to be installed inside the container. The container needs Python 3.9 or newer for the bundled worker.

Keep the campaign directory on a filesystem visible to the execution nodes. The launcher must also reference a runtime, installation, image and bind paths that exist on those nodes. Archiving the script does not archive those resources or freeze a moving container tag.

This example demonstrates the wrapper contract; running it is the site-specific smoke test. It does not establish that a particular CERN or BNL installation is configured correctly.

For local work, enter the environment first and use a local workflow. `%wrapper` is only applied by the queued backends.
