from pathlib import Path

from yawl_run.model import load_spec


def test_load_example():
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root / "examples" / "hello.toml")
    assert spec.name == "hello-yawl"
    assert [t.name for t in spec.tasks] == ["left", "right"]
