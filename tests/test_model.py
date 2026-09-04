from pathlib import Path

import pytest

from yall_run.model import load_spec


def test_load_example():
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root / "examples" / "hello" / "Yallfile")
    assert spec.name == "hello-yall"
    assert [t.name for t in spec.tasks] == ["left", "right", "finish"]


def test_toml_campaigns_are_rejected(tmp_path):
    legacy = tmp_path / "legacy.toml"
    legacy.write_text("[campaign]\nname = 'old'\n")
    with pytest.raises(ValueError, match="TOML campaign files are no longer supported"):
        load_spec(legacy)
