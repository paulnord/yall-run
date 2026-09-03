import json

from yawl_run.campaign import create_campaign
from yawl_run.condor_backend import render_condor
from yawl_run.model import load_spec
from yawl_run.pbs_backend import render_pbs
from yawl_run.slurm_backend import render_slurm


def _manifest(campaign_dir):
    return json.loads((campaign_dir / "campaign.json").read_text())


def test_correlated_each_scientific_graph_is_identical_across_backends(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign backend-matrix\n\n"
        "pedestal-{ped}-{run}-{toa}:\n"
        "    @each ped run toa: \\\n"
        "        296 298 1 \\\n"
        "        299 300 1 \\\n"
        "        328 329 2\n"
        "    @input raw work/converted/rawHGCROC_{run}.root\n"
        "    @input toa configs/ToAOffsets_{toa}.csv\n"
        "    @output pedestal work/pedestal/rawHGCROC_wPed_{ped}.root\n"
        "    ./make-ped {ped} {run} {toa} @input.raw @input.toa @output.pedestal\n\n"
        "transfer-{ped}-{run}-{toa}: pedestal-{ped}-{run}-{toa}\n"
        "    @input pedestal work/pedestal/rawHGCROC_wPed_{ped}.root\n"
        "    @input raw work/converted/rawHGCROC_{run}.root\n"
        "    @input toa configs/ToAOffsets_{toa}.csv\n"
        "    @output result work/transferred/rawHGCROC_{run}.root\n"
        "    ./transfer {ped} {run} {toa} @input.pedestal @input.raw @input.toa @output.result\n"
    )
    spec = load_spec(spec_file)

    campaign_dirs = {
        "local": create_campaign(spec, tmp_path / "campaigns-local", backend="local"),
        "condor": render_condor(spec, tmp_path / "campaigns-condor"),
        "slurm": render_slurm(spec, tmp_path / "campaigns-slurm"),
        "pbs": render_pbs(spec, tmp_path / "campaigns-pbs"),
    }
    manifests = {name: _manifest(path) for name, path in campaign_dirs.items()}

    expected_order = [
        "pedestal-296-298-1",
        "pedestal-299-300-1",
        "pedestal-328-329-2",
        "transfer-296-298-1",
        "transfer-299-300-1",
        "transfer-328-329-2",
    ]
    for backend, manifest in manifests.items():
        assert manifest["backend"] == backend
        assert manifest["task_order"] == expected_order

    reference_tasks = manifests["local"]["tasks"]
    for backend in ("condor", "slurm", "pbs"):
        assert manifests[backend]["tasks"] == reference_tasks

    transfer = reference_tasks["transfer-299-300-1"]
    assert transfer["parents"] == ["pedestal-299-300-1"]
    assert [item["path"] for item in transfer["inputs"]] == [
        str(tmp_path / "work" / "pedestal" / "rawHGCROC_wPed_299.root"),
        str(tmp_path / "work" / "converted" / "rawHGCROC_300.root"),
        str(tmp_path / "configs" / "ToAOffsets_1.csv"),
    ]

    slurm_render = json.loads(
        (campaign_dirs["slurm"] / "slurm" / "render.json").read_text()
    )
    pbs_render = json.loads(
        (campaign_dirs["pbs"] / "pbs" / "render.json").read_text()
    )
    assert slurm_render["experimental"] is True
    assert pbs_render["experimental"] is True
