"""Freeze backend-independent payload execution policy at campaign creation."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from typing import Any

from .model import CampaignSpec
from .paths import logical_absolute


def archive_wrapper(
    spec: CampaignSpec,
    campaign_dir: Path,
) -> dict[str, Any] | None:
    wrapper = spec.execution.wrapper
    if not wrapper:
        return None
    source = logical_absolute(wrapper, spec.source.parent)
    if not source.is_file():
        raise ValueError(f"payload wrapper does not exist: {source}")
    environment_dir = campaign_dir / "environment"
    environment_dir.mkdir(exist_ok=True)
    suffix = "".join(source.suffixes)
    archived = environment_dir / f"payload-wrapper{suffix}"
    shutil.copy2(source, archived)
    archived.chmod(archived.stat().st_mode | 0o100)
    digest = hashlib.sha256(archived.read_bytes()).hexdigest()
    record = {
        "source": str(source),
        "path": str(archived),
        "sha256": digest,
        "size_bytes": archived.stat().st_size,
        "args": list(spec.execution.wrapper_args),
    }
    return record
