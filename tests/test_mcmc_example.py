from pathlib import Path
import os
import py_compile
import subprocess

from yawl_run.model import load_spec


def test_mcmc_example_graph(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / "mcmc"
    monkeypatch.chdir(example)
    spec = load_spec("Yawlfile")

    assert spec.name == "pyroot-langau-mcmc"
    assert len(spec.tasks) == 12
    tasks = {task.name: task for task in spec.tasks}
    chains = [f"chain-{index:02d}" for index in range(8)]
    assert all(name in tasks for name in chains)
    assert tasks["combine"].parents == tuple(chains)
    assert tasks["diagnose"].parents == ("combine",)
    assert tasks["plots"].parents == ("diagnose",)

    for task in spec.tasks:
        assert tuple(task.command[:3]) == ("bash", "run-pyroot.sh", "python3")


def test_mcmc_example_python_syntax(tmp_path):
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / "mcmc"
    for name in ("prepare.py", "run_chain.py", "combine.py", "diagnose.py", "plot.py"):
        py_compile.compile(
            str(example / name),
            cfile=str(tmp_path / f"{name}.pyc"),
            doraise=True,
        )


def test_mcmc_pyroot_launcher_shell_syntax():
    root = Path(__file__).resolve().parents[1]
    launcher = root / "examples" / "mcmc" / "run-pyroot.sh"
    subprocess.run(["bash", "-n", str(launcher)], check=True)


def test_mcmc_pyroot_launcher_execs_payload_directly_in_image(tmp_path):
    root = Path(__file__).resolve().parents[1]
    launcher = root / "examples" / "mcmc" / "run-pyroot.sh"

    image = tmp_path / "eic-image"
    image.touch()
    recorded = tmp_path / "apptainer-args.txt"
    fake_apptainer = tmp_path / "apptainer"
    fake_apptainer.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$YAWL_TEST_ARGS\"\n"
    )
    fake_apptainer.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env.get('PATH', '')}",
            "YAWL_MCMC_FORCE_EIC": "1",
            "YAWL_MCMC_EIC_IMAGE": str(image),
            "YAWL_TEST_ARGS": str(recorded),
        }
    )
    subprocess.run(
        ["bash", str(launcher), "python3", "payload.py", "value with space"],
        check=True,
        env=env,
    )

    assert recorded.read_text().splitlines() == [
        "exec",
        str(image),
        "python3",
        "payload.py",
        "value with space",
    ]


def test_mcmc_pyroot_launcher_outer_eic_shell_uses_separator(tmp_path):
    root = Path(__file__).resolve().parents[1]
    launcher = root / "examples" / "mcmc" / "run-pyroot.sh"

    recorded = tmp_path / "eic-shell-args.txt"
    fake_shell = tmp_path / "eic-shell"
    fake_shell.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$YAWL_TEST_ARGS\"\n"
    )
    fake_shell.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "YAWL_MCMC_FORCE_EIC": "1",
            "YAWL_MCMC_EIC_SHELL": str(fake_shell),
            "YAWL_TEST_ARGS": str(recorded),
        }
    )
    subprocess.run(
        ["bash", str(launcher), "python3", "payload.py"],
        check=True,
        env=env,
    )

    assert recorded.read_text().splitlines() == ["--", "python3", "payload.py"]
