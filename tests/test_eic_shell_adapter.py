import json
from pathlib import Path
import subprocess

from yall_run.condor_backend import render_condor
from yall_run.model import load_spec


def test_eic_shell_adapter_uses_piped_command_and_preserves_argv(tmp_path, monkeypatch):
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

    adapter = Path(__file__).resolve().parents[1] / "examples/eic-shell/run-in-eic-shell.sh"
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign eic-adapter-test\n"
        "backend condor\n"
        "@env EIC_SHELL\n"
        f"%wrapper {adapter} {{EIC_SHELL}} /usr/bin/env EIC_ADAPTER_TEST='two words'\n\n"
        "one:\n"
        "    python3 -c 'import os; print(os.environ[\"EIC_ADAPTER_TEST\"])'\n"
    )

    spec = load_spec(spec_file)
    campaign = render_condor(spec, tmp_path / "campaign root's space")
    node = campaign / "condor" / "yall_0000_one.sh"
    result = subprocess.run(["bash", str(node)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    attempt = campaign / "one_attempt_001"
    assert (attempt / "stdout.log").read_text().strip() == "two words"
    assert json.loads((attempt / "attempt.json").read_text())["state"] == "completed"

    wrapper = json.loads((campaign / "condor/render.json").read_text())["wrapper"]
    assert wrapper["args"] == [
        str(fake_eic_shell),
        "/usr/bin/env",
        "EIC_ADAPTER_TEST=two words",
    ]
