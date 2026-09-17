# Named parameter sets

This local, message-only example needs no scientific software. All values live
at the top of the Yallfile. The independent `checks` list creates the initial
checks; the `pairs` table supplies both the conversion lists and correlated
calibration pairs. One `convert-{run}` family unions both columns. Pedestal
`296` serves two muon runs but is converted and fitted once. Each calibration
waits for its own pedestal fit and muon conversion, with no global barrier.

```sh
cd examples/parameter-sets
yall-run validate
yall-run plan
yall-run create | yall-run start
```

`analyze-{ped}-{run}` inherits its pair from the patterned parent.
`finish` joins all of the resulting analysis tasks. Adding a table row changes
the new campaign without editing any task-body lists.

See [parameter-set syntax](../../docs/PARAMETERS.md) for the validation and
freezing rules.
