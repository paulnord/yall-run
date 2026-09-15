# Use eic-shell from a queued worker

Requires yall-run 0.9.0 or newer, an installed Linux `eic-shell` launcher, and a configured HTCondor/DAGMan host. This checks ROOT and Python without downloading LFHCal data.

Run from the **host shell**, not from inside the container:

```bash
export EIC_SHELL="$HOME/eic/eic-shell"
test -x "$EIC_SHELL"

echo 'root-config --version' | "$EIC_SHELL"
echo 'python3 --version' | "$EIC_SHELL"

cd examples/eic-shell
yall-run validate
yall-run plan
CAMPAIGN=$(yall-run create)
yall-run start "$CAMPAIGN"
yall-run status "$CAMPAIGN"
```

The Yallfile uses a tiny adapter as its archived execution wrapper:

```text
@env EIC_SHELL
%wrapper ./run-in-eic-shell.sh {EIC_SHELL}
```

Why the adapter? The current container-side `eic-shell` treats argv-style commands as arguments to `bash -c "$@"`. For a multi-argument command such as `root-config --version`, that makes `root-config` the command string and `--version` the shell's `$0`; the option never reaches `root-config`. The supported piped-command path does preserve a complete shell command line.

`run-in-eic-shell.sh` therefore shell-quotes every yall worker argv element and sends the resulting command to `eic-shell` on standard input. It uses single-quoted shell words so spaces, wildcard characters, dollar signs, apostrophes, and other ordinary argument content survive the container launcher's line-reading step.

The three tasks check the ROOT and Python versions inside the environment, then report completion. Task stdout is saved in each `<task>_attempt_001/stdout.log`. Launcher startup errors are in the backend's scheduler logs.

Yall archives the adapter itself and freezes the selected `EIC_SHELL` path as a wrapper argument. It does not require yall-run to be installed inside the container. The container needs Python 3.9 or newer for the bundled worker.

Keep the campaign directory on a filesystem visible to the execution nodes. The selected `eic-shell` path must also be visible there and must reference a runtime, installation, image and bind paths that exist on those nodes. Archiving the adapter does not archive those resources or freeze a moving container tag.

This example demonstrates the wrapper contract; running it is the site-specific smoke test. It does not establish that a particular CERN or BNL installation is configured correctly.

For local work, enter the environment first and use a local workflow. `%wrapper` is only applied by the queued backends.
