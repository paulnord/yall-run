from pathlib import Path
import py_compile

from yall_run.model import load_spec


ROOT_EXAMPLES = {
    "muon-lifetime": 25,
    "z-scan": 51,
    "invariant-mass": 13,
}


def test_root_parallel_example_graphs(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    specs = {}
    for name, expected_tasks in ROOT_EXAMPLES.items():
        example = root / "examples" / name
        monkeypatch.chdir(example)
        spec = load_spec("Yallfile")
        specs[name] = spec
        assert len(spec.tasks) == expected_tasks
        for task in spec.tasks:
            assert tuple(task.command[:3]) == (
                "bash",
                "../mcmc/run-pyroot.sh",
                "python3",
            )

    muon = {task.name: task for task in specs["muon-lifetime"].tasks}
    assert muon["fit-00"].parents == ("simulate-00",)
    assert muon["check-00"].parents == ("fit-00",)
    assert len(muon["combine"].parents) == 8

    zscan = {task.name: task for task in specs["z-scan"].tasks}
    assert zscan["scan-00"].parents == ("prepare",)
    assert len(zscan["combine"].parents) == 49

    mass = {task.name: task for task in specs["invariant-mass"].tasks}
    assert mass["reconstruct-00"].parents == ("generate-00",)
    assert len(mass["merge"].parents) == 6


def test_root_parallel_example_python_syntax(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = {
        "muon-lifetime": ("simulate.py", "fit.py", "check.py", "combine.py"),
        "z-scan": ("prepare.py", "evaluate.py", "combine.py"),
        "invariant-mass": ("generate.py", "reconstruct.py", "merge.py"),
    }
    for example_name, names in scripts.items():
        for name in names:
            source = root / "examples" / example_name / name
            py_compile.compile(
                str(source),
                cfile=str(tmp_path / "{}-{}.pyc".format(example_name, name)),
                doraise=True,
            )


def test_root_parallel_examples_declare_graphics():
    root = Path(__file__).resolve().parents[1] / "examples"
    expected = {
        "muon-lifetime": (
            "muon-work/run-{run}-fit.png",
            "muon-work/run-{run}-fit.pdf",
            "muon-work/combined_lifetime.png",
            "muon-work/combined_lifetime.pdf",
        ),
        "z-scan": (
            "z-work/raw_spectrum.png",
            "z-work/raw_spectrum.pdf",
            "z-work/best_fit_overlay.png",
            "z-work/best_fit_overlay.pdf",
            "z-work/nll_surface.png",
            "z-work/nll_surface.pdf",
        ),
        "invariant-mass": (
            "mass-work/run-{run}-fit.png",
            "mass-work/run-{run}-fit.pdf",
            "mass-work/combined_fit.png",
            "mass-work/combined_fit.pdf",
        ),
    }
    for example_name, filenames in expected.items():
        yallfile = (root / example_name / "Yallfile").read_text()
        for filename in filenames:
            assert filename in yallfile


def test_z_scan_includes_generation_truth_grid_point():
    root = Path(__file__).resolve().parents[1]
    grid = root / "examples" / "z-scan" / "grid"
    points = {
        tuple(map(float, path.read_text().split()))
        for path in grid.glob("point-*.txt")
    }
    assert (91.1876, 2.4952) in points
    assert len(points) == 49
