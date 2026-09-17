# Use eic-shell around a scientific payload

Requires a yall-run version with payload-only wrappers, an installed Linux `eic-shell` launcher, and a configured HTCondor/DAGMan host. This checks ROOT and Python without downloading LFHCal data.

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

`run-in-eic-shell.sh` therefore shell-quotes every scientific payload argv element and sends the resulting command to `eic-shell` on standard input. It uses single-quoted shell words so spaces, wildcard characters, dollar signs, apostrophes, and other ordinary argument content survive the container launcher's line-reading step. Literal backslashes are
doubled for the outer `read` loop (which does not use `-r`), then recovered before
the payload shell parses them. This matters when directly wrapping commands
containing shell or Python escape sequences.

The three tasks check the ROOT and Python versions inside the environment, then report completion. Task stdout is saved in each `<task>_attempt_001/stdout.log`. Launcher startup output/errors are now in the same attempt logs. Failures before the host worker starts remain in the backend's scheduler logs.

Yall archives the adapter itself and freezes the selected `EIC_SHELL` path as a wrapper argument. It does not require yall-run to be installed inside the container. Every execution host needs Python 3.9+ for the bundled worker. The container does not need Python for Yall; this particular example requests Python only because one payload is `python3 --version`.

Keep the campaign directory on a filesystem visible to the execution nodes. The selected `eic-shell` path must also be visible there and must reference a runtime, installation, image and bind paths that exist on those nodes. Archiving the adapter does not archive those resources or freeze a moving container tag.

This example demonstrates the wrapper contract; running it is the site-specific smoke test. It does not establish that a particular CERN or BNL installation is configured correctly.

For a local smoke test, stay outside the container and use the same Yallfile:

```bash
yall-run create --backend local | yall-run start
```

The local worker applies the same payload wrapper. Avoid entering `eic-shell`
first, which would unnecessarily nest the launch. Create new campaigns after
updating to this pre-beta behavior; old archived scripts are not migrated.
See [the wrapper contract](../../docs/WRAPPERS.md).
