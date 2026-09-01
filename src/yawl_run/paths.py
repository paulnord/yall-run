from __future__ import annotations

import os
from pathlib import Path


def logical_cwd() -> Path:
    """Return cwd while preserving the shell's logical path when possible."""
    physical = Path.cwd()
    shell_pwd = os.environ.get("PWD")
    if shell_pwd:
        candidate = Path(shell_pwd).expanduser()
        try:
            if candidate.is_absolute() and os.path.samefile(candidate, physical):
                return Path(os.path.normpath(str(candidate)))
        except OSError:
            pass
    return physical


def logical_absolute(value: str | Path, base: Path | None = None) -> Path:
    """Make a path absolute without resolving symlinks or logical mounts."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or logical_cwd()) / path
    return Path(os.path.normpath(str(path)))


def logical_invocation_path(value: str | Path, cwd: Path | None = None) -> Path:
    """Map a physical path beneath cwd back beneath the shell's logical cwd."""
    logical = cwd or logical_cwd()
    path = Path(value).expanduser()
    if not path.is_absolute():
        return logical_absolute(path, logical)
    try:
        relative = path.relative_to(Path.cwd())
    except ValueError:
        return logical_absolute(path, logical)
    return logical_absolute(relative, logical)
