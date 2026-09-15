"""An environment-setting command can follow a container launcher's separator."""

import json
from pathlib import Path
import subprocess

import pytest

from yall_run.condor_backend import render_condor
from yall_run.model import load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.slurm_backend import render_slurm


@pytest.mark.parametrize("backend, render", [
    ("condor", render_condor), ("slurm", render_slurm), ("pbs", render_pbs),
])
def test_thread_limits_are_frozen_wrapper_arguments(tmp_path, monkeypatch, backend, render):
    launcher = tmp_path / "eic-shell"
    launcher.write_text('#!/bin/bash\n[ "$1" = "--" ] || exit 97\nshift\nexec "$@"\n')
    launcher.chmod(0o755)
    monkeypatch.setenv("EIC_SHELL", str(launcher))
    monkeypatch.setenv("ROOT_MAX_THREADS", "99")
    monkeypatch.setenv("OMP_NUM_THREADS", "99")
    recipe = tmp_path / "Yallfile"
    recipe.write_text(
        "campaign thread-limits\nbackend condor\n@env EIC_SHELL\n"
        "%wrapper {EIC_SHELL} -- /usr/bin/env ROOT_MAX_THREADS=1 OMP_NUM_THREADS=1\n"
        "one:\n"
        "    python3 -c 'import os; print(os.environ[\"ROOT_MAX_THREADS\"], os.environ[\"OMP_NUM_THREADS\"])'\n"
    )
    campaign = render(load_spec(recipe), tmp_path / "campaigns")
    script = campaign / backend / "yall_0000_one.sh"
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (campaign / "one_attempt_001/stdout.log").read_text().strip() == "1 1"
    record = json.loads((campaign / "campaign.json").read_text())["execution"][backend]["wrapper"]
    assert record["args"] == ["--", "/usr/bin/env", "ROOT_MAX_THREADS=1", "OMP_NUM_THREADS=1"]
