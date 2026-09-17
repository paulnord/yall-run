import json
from pathlib import Path
import subprocess

import pytest

from yall_run.condor_backend import render_condor
from yall_run.model import load_spec


@pytest.mark.parametrize("payload_python", [True, False])
@pytest.mark.parametrize("adapter_path", [
    "examples/eic-shell/run-in-eic-shell.sh",
    "tests/fixtures/lfhcal/run-in-eic-shell.sh",
])
def test_eic_shell_adapter_uses_piped_command_and_preserves_argv(tmp_path, monkeypatch, adapter_path, payload_python):
    fake_eic_shell = tmp_path / "fake eic-shell"
    fake_eic_shell.write_text(
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "piped_args=()\n"
        "if [ -p /dev/stdin ]; then\n"
        "  while IFS= read line; do\n"
        "    piped_args+=(\"${line}\")\n"
        "  done\n"
        "fi\n"
        "if [ \"${#piped_args[@]}\" != \"0\" ]; then\n"
        "  printf \"%s\\n\" \"${piped_args[@]}\" | bash -s -- --norc --noprofile\n"
        "elif [ $# -gt 0 ]; then\n"
        "  exec bash -c \"$@\"\n"
        "else\n"
        "  exec bash --norc --noprofile\n"
        "fi\n"
    )
    fake_eic_shell.chmod(0o755)
    monkeypatch.setenv("EIC_SHELL", str(fake_eic_shell))

    adapter = Path(__file__).resolve().parents[1] / adapter_path
    spec_file = tmp_path / "Yallfile"
    # A broken/missing payload Python must not affect the host's Yall worker.
    extra = "" if payload_python else " PATH=/no/python PYTHONHOME=/invalid"
    command = (
        "python3 -c 'import os; print(os.environ[\"EIC_ADAPTER_TEST\"])'"
        if payload_python else
        "/bin/sh -c 'command -v python3 >/dev/null 2>&1 && exit 98; printf \"%s\" \"$EIC_ADAPTER_TEST\"'"
    )
    spec_file.write_text(
        "campaign eic-adapter-test\n"
        "backend condor\n"
        "@env EIC_SHELL\n"
        f"%wrapper {adapter} {{EIC_SHELL}} /usr/bin/env EIC_ADAPTER_TEST='two words'{extra}\n\n"
        "one:\n"
        f"    {command}\n"
    )

    spec = load_spec(spec_file)
    campaign = render_condor(spec, tmp_path / "campaign root's space")
    node = campaign / "condor" / "yall_0000_one.sh"
    result = subprocess.run(["bash", str(node)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    attempt = campaign / "one_attempt_001"
    assert (attempt / "stdout.log").read_text().strip() == "two words"
    assert json.loads((attempt / "attempt.json").read_text())["state"] == "completed"

    wrapper = json.loads((campaign / "campaign.json").read_text())["execution"]["wrapper"]
    assert wrapper["args"] == [
        str(fake_eic_shell),
        "/usr/bin/env",
        "EIC_ADAPTER_TEST=two words",
        *([] if payload_python else ["PATH=/no/python", "PYTHONHOME=/invalid"]),
    ]


def test_eic_adapter_preserves_payload_backslashes(tmp_path):
    fake = tmp_path / "fake-eic-shell"
    fake.write_text(
        "#!/bin/bash\n"
        "lines=()\n"
        "while IFS= read line; do lines+=(\"$line\"); done\n"
        "printf '%s\\n' \"${lines[@]}\" | /bin/bash -s\n"
    )
    fake.chmod(0o755)
    adapter = Path(__file__).resolve().parents[1] / "examples/eic-shell/run-in-eic-shell.sh"
    args = [r"a\b", "", "a'b", "$HOME", "ends\\", "line1\nline2"]
    result = subprocess.run(
        [str(adapter), str(fake), "/bin/sh", "-c", 'printf "%s\\n" "$@"', "payload", *args],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "".join(arg + "\n" for arg in args)
