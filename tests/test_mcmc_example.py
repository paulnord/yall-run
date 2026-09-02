from pathlib import Path
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
