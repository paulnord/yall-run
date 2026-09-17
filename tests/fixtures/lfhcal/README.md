# LFHCal adapter snapshot

Exact, unmodified `tools/run-in-eic-shell.sh` from
`paulnord/epic-lfhcal-tbana` at commit
`4b50bfe01e89d02fb791cb7fb9adb3ffd67c0ab8` (`yall-integration`).
Git blob SHA: `17a4e4d3e05b4ec16c29e3756b1c2156f60a2af1`.

Source: https://github.com/paulnord/epic-lfhcal-tbana/blob/4b50bfe01e89d02fb791cb7fb9adb3ffd67c0ab8/tools/run-in-eic-shell.sh

The tests use a simulated eic-shell to exercise its stdin/argv contract.
They do not execute Apptainer or a real EIC image, or require ROOT or CERN.
