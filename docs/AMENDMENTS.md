# Campaign amendments

A campaign normally freezes the expanded workflow at creation time. If an unfinished task later proves to have a bad command, rerunning an expensive campaign from the beginning may be unnecessary. `yall-run amend` provides a narrow, auditable escape hatch without rewriting `campaign.json`.

The ordinary workflow is:

```bash
# Edit the original Yallfile in place.
yall-run amend campaigns/<campaign-id>
yall-run resume campaigns/<campaign-id>
```

`amend` shows the semantic command diff and asks for confirmation before writing anything. Use `--dry-run` to preview without prompting, or `--yes` for a noninteractive/scripted amendment. `--reason` can add human context when the diff itself is not enough.

By default `amend` rereads the source path recorded in `campaign.json.spec_source`. Use `--from PATH` only when the edited Yallfile lives somewhere else.

During comparison, `@env` substitutions use the values frozen when the campaign was created rather than the caller's current shell. This prevents ordinary environment drift from being mistaken for an intentional recipe amendment. Changes to frozen `@set`/`@env` values are rejected in this first version.

## What this first version may change

Amendments are intentionally conservative. Yall compares the edited, expanded Yallfile with the frozen campaign and currently permits only **command changes on unfinished tasks**.

It refuses changes to:

- completed tasks
- task names or ordering
- dependencies
- inputs or outputs
- working directories
- retry policy
- resources
- overwrite policy
- backend defaults
- the payload wrapper

Those changes may invalidate completed work or require scheduler descriptions to be rebuilt, so they belong in a new campaign until a safe amendment rule exists for them.

The command can change on one or more failed, interrupted, blocked, or not-yet-run tasks in one amendment. `--dry-run` performs the comparison without writing anything.

## Immutable base campaign

`campaign.json` and the creation-time archived `Yallfile` are never modified. A successful amendment creates:

```text
amendments/
  0001/
    Yallfile
    amendment.json
```

`Yallfile` is the complete revised recipe used for that amendment. `amendment.json` records the base campaign identity and recipe hash, the revised Yallfile hash, the reason when supplied, the prior amendment in the chain, and the exact before/after command for each affected task.

A later amendment is applied on top of earlier amendments. The before value of every change must match the effective command produced by the preceding chain, so an edited or reordered amendment history is detected rather than silently accepted.

## Attempt provenance

Workers resolve the frozen task and then apply the amendment chain before launching it. The attempt's `provenance.json` records the effective command and the amendment number, path, and SHA-256 that affected that task. Earlier attempts remain untouched, so a failed pre-amendment attempt and a successful post-amendment attempt preserve their distinct launch provenance.

Relational export includes `amendment` and `amendment_change` rows, and `attempt_provenance.amendments_json` records the amendments used by each attempt.

## Queued backends and old campaigns

Condor, Slurm, and PBS campaigns bundle the worker at campaign creation. A queued campaign can therefore use amendments only if that bundled worker contains amendment support. `yall-run amend` refuses older queued campaigns instead of creating an amendment that their frozen worker would ignore.

Local campaigns use the installed worker and do not have this compatibility limitation.

## What amendments do not record

An amendment describes a change to the workflow definition. It does not claim to observe arbitrary changes made outside Yall, such as manually creating a directory, repairing a remote service, changing permissions, or replacing an external executable in place.

If an out-of-band repair is followed by a resume, `yall-run resume --reason "..."` can annotate that recovery round. The note records the operator's explanation without claiming that Yall observed or verified the external action. The amendment record itself still says only what Yall can prove about changes to the workflow definition.
