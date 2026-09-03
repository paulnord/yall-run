import hashlib
import json
from pathlib import Path

from yawl_run.campaign import INPUT_HASH_MAX_BYTES, create_campaign
from yawl_run.model import load_spec


def test_campaign_records_set_values(tmp_path):
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign set-provenance\n"
        "@set run 308\n"
        "@set label \"muon sample\"\n\n"
        "work:\n"
        "    echo {run}\n"
    )

    spec = load_spec(yawlfile)
    assert dict(spec.set_values) == {"run": "308", "label": "muon sample"}

    campaign_dir = create_campaign(spec, tmp_path / "campaigns")
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert manifest["set_values"] == {"run": "308", "label": "muon sample"}


def test_small_existing_declared_input_gets_sha256(tmp_path):
    input_path = tmp_path / "small.dat"
    input_path.write_bytes(b"small scientific input\n")
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign small-input-hash\n\n"
        "work:\n"
        "    @input data small.dat\n"
        "    echo @input.data\n"
    )

    campaign_dir = create_campaign(load_spec(yawlfile), tmp_path / "campaigns")
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    input_record = manifest["tasks"]["work"]["inputs"][0]

    assert manifest["input_hash_policy"] == {
        "algorithm": "sha256",
        "max_bytes": INPUT_HASH_MAX_BYTES,
    }
    assert input_record["path"] == str(input_path)
    assert input_record["creation_fingerprint"] == {
        "size_bytes": input_path.stat().st_size,
        "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
    }


def test_large_declared_input_records_size_but_skips_hash(tmp_path):
    input_path = tmp_path / "large.root"
    with input_path.open("wb") as handle:
        handle.seek(INPUT_HASH_MAX_BYTES)
        handle.write(b"x")
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign large-input-hash\n\n"
        "work:\n"
        "    @input data large.root\n"
        "    echo @input.data\n"
    )

    campaign_dir = create_campaign(load_spec(yawlfile), tmp_path / "campaigns")
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    fingerprint = manifest["tasks"]["work"]["inputs"][0]["creation_fingerprint"]

    assert fingerprint == {
        "size_bytes": INPUT_HASH_MAX_BYTES + 1,
        "sha256_skipped": "size_limit",
    }


def test_missing_or_future_input_has_no_creation_fingerprint(tmp_path):
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign future-input\n\n"
        "work:\n"
        "    @input data produced-later.dat\n"
        "    echo @input.data\n"
    )

    campaign_dir = create_campaign(load_spec(yawlfile), tmp_path / "campaigns")
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    input_record = manifest["tasks"]["work"]["inputs"][0]

    assert input_record["path"] == str(tmp_path / "produced-later.dat")
    assert "creation_fingerprint" not in input_record
