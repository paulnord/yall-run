from pathlib import Path

import yall_run.version as version


def test_package_version_is_alpha():
    from yall_run import __version__

    assert __version__ == "0.10.0a1"


def test_display_version_without_checkout(monkeypatch):
    monkeypatch.setattr(version, "_checkout_root", lambda: None)
    assert version.display_version() == "yall-run 0.10.0a1"


def test_display_version_includes_checkout_revision(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(version, "_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(version, "_git_revision", lambda root: "abcdef0")
    assert version.display_version() == "yall-run 0.10.0a1 (abcdef0)"


def test_pyproject_version_matches_package():
    from yall_run import __version__

    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text()
    assert f'version = "{__version__}"' in text
