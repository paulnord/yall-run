import hashlib
import json
from pathlib import Path

from yawl_run.campaign import create_campaign
from yawl_run.model import load_spec


def test_campaign_records_explicit_executable_identity(tmp_path):
    tool = tmp_path / "tool.sh"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    original = tool.read_bytes()

    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign executable-provenance\n\n"
        "work:\n"
        "    ./tool.sh\n"
    )

    campaign_dir = create_campaign(
        load_spec(spec_file), tmp_path / "campaigns", backend="local"
    )
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    executable = manifest["tasks"]["work"]["executable"]

    assert executable == {
        "argv0": "./tool.sh",
        "path": str(tool),
        "realpath": str(tool.resolve()),
        "resolution": "explicit",
        "resolved": True,
        "sha256": hashlib.sha256(original).hexdigest(),
        "size_bytes": len(original),
    }

    tool.write_text("#!/bin/sh\nexit 17\n")
    frozen = json.loads((campaign_dir / "campaign.json").read_text())
    assert frozen["tasks"]["work"]["executable"]["sha256"] == hashlib.sha256(
        original
    ).hexdigest()


def test_campaign_records_path_resolved_executable_identity(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "yawl-test-tool"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign executable-path-provenance\n\n"
        "work:\n"
        "    yawl-test-tool\n"
    )

    campaign_dir = create_campaign(
        load_spec(spec_file), tmp_path / "campaigns", backend="local"
    )
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    executable = manifest["tasks"]["work"]["executable"]

    assert executable["argv0"] == "yawl-test-tool"
    assert executable["resolution"] == "PATH"
    assert executable["path"] == str(tool)
    assert executable["realpath"] == str(tool.resolve())
    assert executable["resolved"] is True
    assert executable["sha256"] == hashlib.sha256(tool.read_bytes()).hexdigest()


def test_shell_command_does_not_guess_a_single_executable(tmp_path):
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign shell-executable-provenance\n\n"
        "work:\n"
        "    ! echo hello > output.txt\n"
    )

    campaign_dir = create_campaign(
        load_spec(spec_file), tmp_path / "campaigns", backend="local"
    )
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert "executable" not in manifest["tasks"]["work"]
