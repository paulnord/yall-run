from __future__ import annotations

import json
import os

from yall_run import worker
from yall_run.campaign import create_campaign
from yall_run.model import load_spec


def test_parse_linux_cpuinfo_extracts_identity_and_features():
    cpuinfo = """processor   : 0
vendor_id   : GenuineIntel
cpu family  : 6
model       : 106
model name  : Intel(R) Xeon(R) Test CPU
stepping    : 6
microcode   : 0xd0003a5
flags       : fpu sse2 avx avx2 fma

processor   : 1
vendor_id   : GenuineIntel
"""
    assert worker._parse_linux_cpuinfo(cpuinfo) == {
        "vendor_id": "GenuineIntel",
        "model_name": "Intel(R) Xeon(R) Test CPU",
        "family": 6,
        "model": 106,
        "stepping": 6,
        "microcode": "0xd0003a5",
        "features": ["fpu", "sse2", "avx", "avx2", "fma"],
    }


def test_condor_context_records_job_queue_time_and_machine_ad(tmp_path, monkeypatch):
    job_ad = tmp_path / "job.ad"
    job_ad.write_text(
        'ClusterId = 38957\n'
        'ProcId = 0\n'
        'RemoteHost = "slot1_17@spool0868.sdcc.bnl.gov"\n'
        'QDate = 1789850000\n'
    )
    machine_ad = tmp_path / "machine.ad"
    machine_ad.write_text(
        'Name = "slot1_17@spool0868.sdcc.bnl.gov"\n'
        'Machine = "spool0868.sdcc.bnl.gov"\n'
        'SlotID = 1\n'
        'Arch = "X86_64"\n'
        'OpSys = "LINUX"\n'
        'OpSysAndVer = "AlmaLinux9"\n'
        'CpuFamily = 6\n'
        'CpuModelNumber = 106\n'
        'Cpus = 1\n'
        'Memory = 4096\n'
    )
    monkeypatch.setenv("_CONDOR_JOB_AD", str(job_ad))
    monkeypatch.setenv("_CONDOR_MACHINE_AD", str(machine_ad))

    job = worker._condor_job_context()
    machine = worker._condor_machine_context()

    assert job is not None
    assert job["job_id"] == "38957.0"
    assert job["qdate"] == 1789850000
    assert job["remote_host"] == "slot1_17@spool0868.sdcc.bnl.gov"
    assert len(job["job_ad_sha256"]) == 64

    assert machine is not None
    assert machine["machine"] == "spool0868.sdcc.bnl.gov"
    assert machine["cpu_family"] == 6
    assert machine["cpu_model_number"] == 106
    assert machine["cpus"] == 1
    assert machine["memory_mb"] == 4096
    assert len(machine["machine_ad_sha256"]) == 64


def test_runtime_environment_is_allowlisted(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    monkeypatch.setenv("ROOT_MAX_THREADS", "1")
    monkeypatch.setenv("YALL_TEST_SECRET", "do-not-record")

    environment = worker._runtime_environment()

    assert environment["OMP_NUM_THREADS"] == "2"
    assert environment["ROOT_MAX_THREADS"] == "1"
    assert "YALL_TEST_SECRET" not in environment


def test_attempt_provenance_records_runtime_context(tmp_path, monkeypatch):
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign runtime-provenance\n\n"
        "one:\n"
        "    /bin/true\n"
    )
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    monkeypatch.setattr(
        worker,
        "_host_machine_context",
        lambda: {
            "architecture": "X86_64",
            "logical_cpu_count": 64,
            "affinity": {"count": 1, "cpus": [17]},
            "cpu": {
                "vendor_id": "GenuineIntel",
                "model_name": "Test Xeon",
                "family": 6,
                "model": 106,
                "stepping": 6,
            },
        },
    )
    monkeypatch.setattr(
        worker, "_runtime_environment", lambda: {"OMP_NUM_THREADS": "1"}
    )
    monkeypatch.setattr(
        worker,
        "_resource_limits",
        lambda: {"stack": {"soft": 8388608, "hard": "infinity"}},
    )

    assert worker.run_task(campaign_dir, "one") == 0

    attempt_dir = campaign_dir / "one_attempt_001"
    provenance = json.loads((attempt_dir / "provenance.json").read_text())
    attempt = json.loads((attempt_dir / "attempt.json").read_text())

    assert provenance["schema"] == 2
    assert provenance["execution"]["machine"]["cpu"]["vendor_id"] == "GenuineIntel"
    assert provenance["execution"]["machine"]["affinity"]["cpus"] == [17]
    assert provenance["execution"]["environment"] == {"OMP_NUM_THREADS": "1"}
    assert provenance["execution"]["resource_limits"]["stack"]["soft"] == 8388608

    timing = attempt["timing"]
    assert timing["real_seconds"] >= 0.0
    if hasattr(os, "wait4"):
        usage = timing["resource_usage"]
        assert usage["max_rss"] >= 0
        assert usage["minor_page_faults"] >= 0
        assert usage["voluntary_context_switches"] >= 0
