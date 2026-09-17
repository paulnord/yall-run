from __future__ import annotations

from pathlib import Path
import subprocess

from . import __version__


def _checkout_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() else None


def _git_revision(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    revision = result.stdout.strip()
    if not revision:
        return None

    try:
        dirty = subprocess.run(
            ["git", "-C", str(root), "diff-index", "--quiet", "HEAD", "--"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        dirty = None
    if dirty is not None and dirty.returncode == 1:
        revision += "+dirty"
    return revision


def display_version() -> str:
    """Return the package version plus checkout identity when available."""
    text = f"yall-run {__version__}"
    root = _checkout_root()
    if root is None:
        return text
    revision = _git_revision(root)
    return f"{text} ({revision})" if revision else text
