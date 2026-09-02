import json
import subprocess

import pytest

from yawl_run.model import load_spec
from yawl_run.pbs_backend import render_pbs
from yawl_run.slurm_backend import render_slurm


@pytest.mark.parametrize(
    ("backend", "renderer"),
    (("slurm", render_slurm), ("pbs", render_pbs)),
)
def test_generated_batch_script_executes_bundled_worker(tmp_path, backend, renderer):
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        f"campaign {backend}-script-test\n"
        f"backend {backend}\n\n"
        "hello:\n"
        "    %retry 1\n"
        "    @output result result.txt\n"
        "    ! printf hello > @output.result\n"
    )
    campaign_dir = renderer(load_spec(spec_file), tmp_path / "campaigns")
    script = next((campaign_dir / backend).glob("yawl_0000_*.sh"))

    syntax = subprocess.run(
        ["bash", "-n", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    proc = subprocess.run(
        ["bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    task = json.loads((campaign_dir / "tasks" / "hello.json").read_text())
    attempt = json.loads(
        (campaign_dir / "hello_attempt_001" / "attempt.json").read_text()
    )
    assert task["state"] == "completed"
    assert task["attempts"] == 1
    assert attempt["state"] == "completed"
    assert attempt["timing"]["real_seconds"] >= 0
    assert (tmp_path / "result.txt").read_text() == "hello"
