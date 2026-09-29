from pathlib import Path
import re


def edit(name, old, new, count=1):
    p = Path(name)
    text = p.read_text()
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f'{name}: expected {count} matches, found {actual}: {old[:100]!r}')
    p.write_text(text.replace(old, new))


model = 'src/yall_run/model.py'
edit(model, '    postflight: Tuple[Command, ...] = ()\n', '''    postflight: Tuple[Command, ...] = ()
    account_provenance: str = "off"
    # Retain the recipe choice when a creation-time CLI override is used.
    account_provenance_recipe: str | None = None

    def __post_init__(self) -> None:
        if self.account_provenance not in ("off", "full"):
            raise ValueError("account provenance must be off or full")
        if (self.account_provenance_recipe is not None
                and self.account_provenance_recipe not in ("off", "full")):
            raise ValueError("recipe account provenance must be off or full")
''')

syntax = 'src/yall_run/syntax.py'
edit(syntax, 'def _parse(text: str) -> Tuple[str, str, CondorSpec, ExecutionSpec,\n                               Tuple[Command, ...], List[_TaskTemplate]]:', '''def _parse(text: str) -> Tuple[str, str, CondorSpec, ExecutionSpec, str,
                               Tuple[Command, ...], Tuple[Command, ...], List[_TaskTemplate]]:''')
edit(syntax, '    condor_getenv = True\n', '    condor_getenv = True\n    account_provenance = "off"\n    account_provenance_seen = False\n')
edit(syntax, '                elif directive == "wrapper":\n', '''                elif directive == "account-provenance":
                    if tasks:
                        raise ValueError(f"line {lineno}: %account-provenance must appear before tasks")
                    if account_provenance_seen:
                        raise ValueError(f"line {lineno}: duplicate %account-provenance directive")
                    if len(values) != 1 or values[0] not in ("off", "full"):
                        raise ValueError(f"line {lineno}: %account-provenance requires off or full")
                    account_provenance = values[0]
                    account_provenance_seen = True
                elif directive == "wrapper":
''')
edit(syntax, '    return campaign_name, backend, condor, execution, preflight, postflight, tasks\n', '    return campaign_name, backend, condor, execution, account_provenance, preflight, postflight, tasks\n')
edit(syntax, '    campaign_name, backend, condor, execution, preflight, postflight, templates = _parse(source.read_text())\n', '    campaign_name, backend, condor, execution, account_provenance, preflight, postflight, templates = _parse(source.read_text())\n')
edit(syntax, '        execution=execution,\n', '        execution=execution,\n        account_provenance=account_provenance,\n')

worker = 'src/yall_run/worker.py'
edit(worker, 'def user_identity() -> dict[str, Any]:', '''def account_provenance_mode(manifest: dict[str, Any]) -> str:
    """Read the frozen policy; legacy campaigns default to no new collection."""
    policy = manifest.get("provenance_policy", {})
    if not isinstance(policy, dict):
        raise ValueError("invalid account provenance policy: expected an object")
    mode = policy.get("accounts", "off")
    if mode not in ("off", "full"):
        raise ValueError("account provenance must be off or full")
    return mode


def user_identity(mode: str = "off") -> dict[str, Any]:''')
edit(worker, '    result: dict[str, Any] = {\n        "username": None,', '''    if mode not in ("off", "full"):
        raise ValueError("account provenance must be off or full")
    if mode == "off":
        # Do not even query OS IDs or import the account database when disabled.
        return {"recorded": False, "reason": "disabled_by_policy"}
    result: dict[str, Any] = {
        "username": None,''')
edit(worker, '    manifest = _read_json(manifest_path)\n', '    manifest = _read_json(manifest_path)\n    account_mode = account_provenance_mode(manifest)\n')
edit(worker, '            "user": user_identity(),\n', '            "user": user_identity(account_mode),\n')

campaign = 'src/yall_run/campaign.py'
edit(campaign, 'from .worker import run_task, user_identity\n', 'from .worker import account_provenance_mode, run_task, user_identity\n')
edit(campaign, '    campaign_dir, manifest = campaign_manifest(campaign_dir)\n    _validate_unstarted(campaign_dir, manifest)\n', '    campaign_dir, manifest = campaign_manifest(campaign_dir)\n    account_mode = account_provenance_mode(manifest)\n    _validate_unstarted(campaign_dir, manifest)\n')
edit(campaign, '        "requested_at": _utc_now(),\n        "hostname": platform.node(),\n        "user": user_identity(),\n', '        "requested_at": _utc_now(),\n        "hostname": platform.node(),\n        "provenance_policy": {"accounts": account_mode},\n        "user": user_identity(account_mode),\n')
edit(campaign, 'def begin_campaign(campaign_dir: str | Path, *, overwrite: bool = False) -> Path:\n    campaign_dir, manifest = campaign_manifest(campaign_dir)\n', 'def begin_campaign(campaign_dir: str | Path, *, overwrite: bool = False) -> Path:\n    campaign_dir, manifest = campaign_manifest(campaign_dir)\n    account_mode = account_provenance_mode(manifest)\n')
edit(campaign, '        start_user = pending.get("user")\n', '''        if ("provenance_policy" in pending
                and account_provenance_mode(pending) != account_mode):
            raise ValueError("account provenance policy changed during submission")
        start_user = user_identity("off") if account_mode == "off" else pending.get("user")
''')
edit(campaign, '        start_user = user_identity()\n', '        start_user = user_identity(account_mode)\n')
edit(campaign, '        "hostname": start_hostname,\n', '        "hostname": start_hostname,\n        "provenance_policy": {"accounts": account_mode},\n')
edit(campaign, '    cwd: Path, workflow_dir: Path | None = None, require_success: bool = True,\n', '    cwd: Path, workflow_dir: Path | None = None, require_success: bool = True,\n    account_provenance: str = "off",\n')
edit(campaign, '    """Run a host-side lifecycle hook and archive one record per command."""\n    records = []\n', '    """Run a host-side lifecycle hook and archive one record per command."""\n    account_mode = account_provenance_mode({"provenance_policy": {"accounts": account_provenance}})\n    records = []\n')
edit(campaign, '            "user": user_identity(),\n', '            "user": user_identity(account_mode),\n')
edit(campaign, '    return _run_hook(spec.preflight, campaign_dir, hook="preflight", cwd=spec.source.parent)\n', '    return _run_hook(spec.preflight, campaign_dir, hook="preflight", cwd=spec.source.parent,\n                     account_provenance=spec.account_provenance)\n')
edit(campaign, '        workflow_dir=workflow_dir,\n', '        workflow_dir=workflow_dir,\n        account_provenance=account_provenance_mode(manifest),\n')
edit(campaign, '    creation_user = user_identity()\n', '    creation_user = user_identity(spec.account_provenance)\n')
edit(campaign, '        "created_at": created_at,\n', '''        "created_at": created_at,
        "provenance_policy": {
            "accounts": spec.account_provenance,
            "recipe_accounts": (spec.account_provenance_recipe
                                if spec.account_provenance_recipe is not None
                                else spec.account_provenance),
        },
''')
edit(campaign, '        raise ValueError("resume currently supports local campaigns only")\n', '        raise ValueError("resume currently supports local campaigns only")\n    account_mode = account_provenance_mode(manifest)\n')
edit(campaign, '        "user": user_identity(),\n', '        "user": user_identity(account_mode),\n')

cli = 'src/yall_run/cli.py'
edit(cli, 'from dataclasses import asdict\n', 'from dataclasses import asdict, replace\n')
edit(cli, 'from .worker import run_task\n', 'from .worker import account_provenance_mode, run_task\n')
edit(cli, '    create.add_argument("--backend", choices=("local", "condor", "slurm", "pbs"))\n', '''    create.add_argument("--backend", choices=("local", "condor", "slurm", "pbs"))
    create.add_argument(
        "--account-provenance", choices=("off", "full"),
        help="override and freeze OS-account recording (default: recipe, otherwise off); "
             "does not anonymize paths, commands, logs, or scheduler records",
    )
''')
edit(cli, '        "source": str(spec.source),\n', '        "source": str(spec.source),\n        "provenance_policy": {"accounts": spec.account_provenance},\n')
edit(cli, '            print(f"Campaign: {spec.name} (backend={spec.backend})")\n', '            print(f"Campaign: {spec.name} (backend={spec.backend})")\n            print(f"Account provenance: {spec.account_provenance}")\n')
edit(cli, '            spec = load_spec(args.spec)\n            backend = args.backend or spec.backend\n', '''            spec = load_spec(args.spec)
            if args.account_provenance is not None:
                spec = replace(spec, account_provenance=args.account_provenance,
                               account_provenance_recipe=spec.account_provenance)
            backend = args.backend or spec.backend
''')
edit(cli, '            if backend == "condor":\n                cdir = render_condor', '            print(f"Account provenance: {spec.account_provenance}", file=sys.stderr)\n            if backend == "condor":\n                cdir = render_condor')
edit(cli, '            cdir, manifest = campaign_manifest(campaign_dir)\n            backend = manifest.get("backend", "local")\n', '            cdir, manifest = campaign_manifest(campaign_dir)\n            print(f"Account provenance: {account_provenance_mode(manifest)}", file=sys.stderr)\n            backend = manifest.get("backend", "local")\n')

amend = 'src/yall_run/amend.py'
edit(amend, 'def _validate_wrapper(spec: CampaignSpec, manifest: dict[str, Any]) -> None:\n', '''def _validate_wrapper(spec: CampaignSpec, manifest: dict[str, Any]) -> None:
    policy = manifest.get("provenance_policy") or {}
    recipe_accounts = policy.get("recipe_accounts", policy.get("accounts", "off"))
    if spec.account_provenance != recipe_accounts:
        raise ValueError("current Yallfile changes frozen account provenance; create a new campaign")
''')

post = 'src/yall_run/postflight_runner.py'
edit(post, 'import json\n', 'import inspect\nimport json\n', count=1)
edit(post, 'from pathlib import Path\n', 'from pathlib import Path\n\nfrom .worker import account_provenance_mode, user_identity\n')
edit(post, '    payload = json.dumps(commands, separators=(",", ":"))\n', '    payload = json.dumps(commands, separators=(",", ":"))\n    account_helpers = "\\n\\n".join(inspect.getsource(fn) for fn in (account_provenance_mode, user_identity))\n')
edit(post, 'import datetime, json, os, pathlib, subprocess, sys\n', 'from __future__ import annotations\nimport datetime, json, os, pathlib, platform, subprocess, sys\nfrom typing import Any\n\n{account_helpers}\n')
edit(post, 'root = campaign / "postflight"\n', 'account_mode = account_provenance_mode(json.loads((campaign / "campaign.json").read_text()))\nroot = campaign / "postflight"\n')
edit(post, '               "cwd": str(campaign), "started_at": now()}}\n', '               "cwd": str(campaign), "started_at": now(),\n               "hostname": platform.node(), "user": user_identity(account_mode)}}\n')

p = Path('tests/test_user_identity.py')
s = p.read_text().replace('worker.user_identity()', 'worker.user_identity("full")')
s = s.replace('f"campaign account-test\\nbackend {backend}\\n\\none:\\n"', 'f"campaign account-test\\nbackend {backend}\\n%account-provenance full\\n\\none:\\n"')
s = re.sub(r'lambda: (creator|submitter|executor|first|second|resumer|actor|identity\()', r'lambda mode="full": \1', s)
s = s.replace('def unexpected():', 'def unexpected(*args):')
s = s.replace('hook=hook, cwd=tmp_path)', 'hook=hook, cwd=tmp_path, account_provenance="full")')
p.write_text(s)

comment = "# Set to full to record operator accounts when permitted by your site's privacy policy.\n%account-provenance off\n"
for family in ('mcmc', 'muon-lifetime', 'invariant-mass', 'z-scan', 'golomb'):
    root = Path('examples') / family
    for path in root.rglob('Yallfile*'):
        if not path.is_file():
            continue
        text = path.read_text()
        if not re.search(r'^campaign ', text, re.M):
            continue
        if '%account-provenance' in text:
            raise RuntimeError(f'Unexpected policy already in {path}')
        text = re.sub(r'(^backend [^\n]+\n)', lambda m: m[1] + '\n' + comment, text, count=1, flags=re.M)
        if '%account-provenance' not in text:
            text = re.sub(r'(^campaign [^\n]+\n)', lambda m: m[1] + '\n' + comment, text, count=1, flags=re.M)
        path.write_text(text)

p = Path('docs/PROVENANCE.md')
s = p.read_text()
a, rest = s.split('## User account provenance\n', 1)
_, b = rest.split('## Attempt provenance\n', 1)
p.write_text(a + '''## User account provenance

Starting with **0.12.0a7**, OS-account snapshots are **off by default**.
Choose once at campaign level, before the tasks:

```text
# Set to full to record operator accounts when permitted by your site's privacy policy.
%account-provenance off
```

Use `full` for deliberate operator attribution, or override the recipe at creation:

```bash
yall-run create --account-provenance full
```

The explicit CLI choice wins over the Yallfile, which otherwise defaults to `off`.
The resolved choice is frozen in `campaign.json` at `provenance_policy.accounts`.
`recipe_accounts` retains the recipe's choice so command-only amendments remain
possible after a CLI override. Editing the recipe cannot change a frozen policy.
`plan` displays the recipe policy; `create` and `start` report the resolved policy
on stderr, preserving create's single-path stdout and structured plan output.

The policy is resolved before preflight and before collecting any identity. The
same frozen choice governs creation, submission, attempts (including failures
and retries), local resumes, and local or standalone scheduler lifecycle hooks.
No live environment variable enables or disables recording on workers.

| Record | Field | Account being recorded when full |
| --- | --- | --- |
| `campaign.json` | `creation.user` | Campaign creator |
| `start.json` | `user` | Initial starter/submitter |
| `<task>_attempt_NNN/provenance.json` | `execution.user` | Host worker for this attempt |

With `off`, Yall does not query OS-account IDs or the account database. Each
snapshot instead contains `{"recorded": false, "reason": "disabled_by_policy"}`.
This distinguishes intentional omission from an unavailable account lookup.
Invalid modes fail before running work; they never fall back to full collection.

With `full`, snapshots contain `username`, `effective_username`, `uid`, `gid`,
`euid`, `egid`, and name-lookup source fields. Names come from `pwd.getpwuid`
using the process's real/effective IDs, not inherited `USER`, `LOGNAME`, or
`SUDO_USER`. Unavailable values stay null with diagnostics; missing POSIX APIs
or account databases do not prevent execution. No full account entry is copied.

The submission snapshot is captured before scheduling in `state/start-pending.json`
and retained at finalization. A legacy pending snapshot without an account stays
unknown when full recording is selected, rather than being attributed to the
finalizer. An off campaign always writes the omission marker.

These describe host process accounts, not the human behind sudo/shared accounts
or a possibly different account inside a payload container. Interpret IDs with
the hostname. These are observations, not authenticated or tamper-proof auditing.

**This is not an anonymization switch.** Paths, command arguments, logs, scheduler
records, and application outputs may still identify people. It does not redact
old records or control what tasks, hooks, wrappers, or the scheduler write.
Access controls, retention, notices, and review before sharing remain site concerns.
Redacted export is a separate feature and is not implemented by this switch.

```bash
jq '.provenance_policy, .creation.user' "$CAMPAIGN/campaign.json"
jq '.user' "$CAMPAIGN/start.json"
jq '.execution.user' "$CAMPAIGN"/*_attempt_*/provenance.json
```

For legacy campaigns lacking a policy, updated code defaults to off for additional
snapshots. Existing history is not erased or backfilled. Already archived workers
and hook runners are unchanged by upgrading an installation; create a fresh
campaign for consistent policy enforcement. SQL/CSV exports are not anonymized.

## Attempt provenance
''' + b)
p = Path('docs/YALLFILE.md')
p.write_text(p.read_text().rstrip() + '''

## Account provenance policy

`%account-provenance off|full` is a campaign-only directive placed before tasks.
It may appear once. The default is `off`, which skips explicit OS-account
collection rather than collecting and then filtering it. Use `full` when
operator attribution is appropriate under the site's privacy policy.

```text
# Set to full to record operator accounts when permitted by your site's privacy policy.
%account-provenance off
```

`yall-run create --account-provenance full` (or `off`) overrides the recipe and
freezes the resolved choice into the campaign. No start-time or worker-environment
override is supported. An amendment cannot change the recipe's policy; create
a new campaign instead. This option does not anonymize paths, commands, logs,
or scheduler records. See [PROVENANCE.md](PROVENANCE.md#user-account-provenance).
''')
print('Applied account privacy source, documentation, and example changes.')
