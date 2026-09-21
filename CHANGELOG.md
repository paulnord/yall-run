# Changelog

## Unreleased

- Add repeatable campaign-level `%preflight` commands for host setup during
  `create`, with ordered execution, per-command logs and results, and failure
  blocking campaign creation. Validation and planning never execute them.
- Keep setup outside payload wrappers and scheduler graphs, preserve the
  campaign-path-only stdout interface, and prevent setup reruns or amendments.

## 0.11.0a1

- Explicit and named-source `@each` bindings may bind a subset of task-name
  placeholders. Compatible patterned parents supply the remaining values.
- Explicit bindings filter parent inheritance, allowing a pedestal task to
  inherit only pedestal runs while a muon merge retains muon-only fan-in.
- Fully specified `@each`, named-source ordered unions, and duplicate-row
  handling retain their existing behavior. File-pattern `@each` still requires
  all task-name placeholders.
- Added regression tests and syntax documentation for partial binding,
  incompatible parent rows, and disagreement between patterned parents.
- Includes Python 3.8 support, composed static-variable resolution, and CI
  action updates added since 0.10.0a1.

This remains an alpha release. Existing frozen campaigns are not rewritten;
create new campaigns to use revised workflow definitions.
