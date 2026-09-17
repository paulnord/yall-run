import hashlib
import json
from pathlib import Path

from yall_run.campaign import create_campaign
from yall_run.model import load_spec
from yall_run.worker import run_task


def test_worker_records_condor_execution_identity(tmp_path, monkeypatch):
    spec_path = tmp_path / "Yallfile"
    spec_path.write_text(
        "campaign trace-provenance\n"
        "backend condor\n\n"
        "one:\n"
        "    /bin/true\n"
    )
    campaign = create_campaign(load_spec(spec_path), tmp_path / "campaigns", backend="condor")
    ad = tmp_path / ".job.ad"
    ad.write_text(
        'ClusterId = 44114\n'
        'ProcId = 0\n'
        'GlobalJobId = "schedd.example#44114.0#1789640000"\n'
        'DAGManJobId = 44078\n'
        'DAGNodeName = "yall_0000_one"\n'
        'YallDAGRetry = 2\n'
        'NumJobStarts = 3\n'
        'RemoteHost = "slot1@worker31"\n'
        'JobCurrentStartDate = 1789640123\n'
    )
    monkeypatch.setenv("_CONDOR_JOB_AD", str(ad))

    assert run_task(campaign, "one") == 0
    provenance = json.loads((campaign / "one_attempt_001" / "provenance.json").read_text())
    scheduler = provenance["scheduler"]
    assert scheduler["backend"] == "condor"
    assert scheduler["job_id"] == "44114.0"
    assert scheduler["global_job_id"] == "schedd.example#44114.0#1789640000"
    assert scheduler["dagman_job_id"] == 44078
    assert scheduler["dag_node_name"] == "yall_0000_one"
    assert scheduler["dag_retry"] == 2
    assert scheduler["num_job_starts"] == 3
    assert scheduler["remote_host"] == "slot1@worker31"
    assert scheduler["job_current_start_date"] == 1789640123
    assert scheduler["job_ad_path"] == str(ad)
    assert scheduler["job_ad_sha256"] == hashlib.sha256(ad.read_bytes()).hexdigest()


def test_worker_records_unreadable_condor_job_ad_without_failing_task(tmp_path, monkeypatch):
    spec_path = tmp_path / "Yallfile"
    spec_path.write_text(
        "campaign trace-provenance-missing\n"
        "backend condor\n\n"
        "one:\n"
        "    /bin/true\n"
    )
    campaign = create_campaign(load_spec(spec_path), tmp_path / "campaigns", backend="condor")
    missing = tmp_path / "does-not-exist.ad"
    monkeypatch.setenv("_CONDOR_JOB_AD", str(missing))

    assert run_task(campaign, "one") == 0
    provenance = json.loads((campaign / "one_attempt_001" / "provenance.json").read_text())
    scheduler = provenance["scheduler"]
    assert scheduler["backend"] == "condor"
    assert scheduler["job_ad_path"] == str(missing)
    assert "read_error" in scheduler



def test_job_identity_is_case_insensitive_and_literals_only(tmp_path, monkeypatch):
    from yall_run.worker import _condor_job_context
    ad = tmp_path / 'job.ad'
    ad.write_text('clusterid = 42\npRoCiD = 0\nYALLDAGRETRY = 2\n'
                  'NumJobStarts = undefined\nRemoteHost = TARGET.Name\n'
                  'SecretToken = "must not be recorded"\n')
    monkeypatch.setenv('_CONDOR_JOB_AD', str(ad))
    result = _condor_job_context()
    assert result['job_id'] == '42.0'
    assert result['dag_retry'] == 2
    assert 'num_job_starts' not in result
    assert 'remote_host' not in result
    assert set(result['parse_errors']) == {'num_job_starts', 'remote_host'}
    assert 'must not be recorded' not in json.dumps(result)


def test_malformed_identity_does_not_fabricate_job_id(tmp_path, monkeypatch):
    from yall_run.worker import _condor_job_context
    ad = tmp_path / 'job.ad'
    monkeypatch.setenv('_CONDOR_JOB_AD', str(ad))
    for value in ('undefined', 'error', '1 + 2', 'true', '-1', '"42"', '"bad\\xZZ"', '9' * 5000):
        ad.write_text(f'ClusterId = {value}\nProcId = 0\n')
        result = _condor_job_context()
        assert 'job_id' not in result, value
        assert 'cluster_id' in result['parse_errors'], value


def test_duplicate_identity_is_ambiguous(tmp_path, monkeypatch):
    from yall_run.worker import _condor_job_context
    ad = tmp_path / 'job.ad'
    ad.write_text('ClusterId = 42\nCLUSTERID = 43\nProcId = 0\n')
    monkeypatch.setenv('_CONDOR_JOB_AD', str(ad))
    result = _condor_job_context()
    assert 'job_id' not in result
    assert result['parse_errors']['cluster_id'] == 'duplicate attribute'


def test_job_ad_reads_are_bounded(tmp_path, monkeypatch):
    from yall_run.worker import _condor_job_context, _CONDOR_JOB_AD_MAX_BYTES
    ad = tmp_path / 'job.ad'
    ad.write_bytes(b'x' * (_CONDOR_JOB_AD_MAX_BYTES + 1))
    monkeypatch.setenv('_CONDOR_JOB_AD', str(ad))
    result = _condor_job_context()
    assert 'limit' in result['read_error']
    assert 'job_ad_sha256' not in result


def test_job_ad_special_file_is_not_read(tmp_path, monkeypatch):
    import os
    from yall_run.worker import _condor_job_context
    fifo = tmp_path / 'job.ad'
    os.mkfifo(fifo)
    monkeypatch.setenv('_CONDOR_JOB_AD', str(fifo))
    assert 'regular file' in _condor_job_context()['read_error']


def test_absent_job_ad_is_optional(monkeypatch):
    from yall_run.worker import _condor_job_context
    monkeypatch.delenv('_CONDOR_JOB_AD', raising=False)
    assert _condor_job_context() is None


def test_local_task_does_not_inherit_outer_condor_identity(tmp_path, monkeypatch):
    spec = tmp_path / 'Yallfile'
    spec.write_text('campaign nested\nbackend local\none:\n    /bin/true\n')
    campaign = create_campaign(load_spec(spec), tmp_path / 'campaigns')
    ad = tmp_path / 'outer.ad'
    ad.write_text('ClusterId = 42\nProcId = 0\n')
    monkeypatch.setenv('_CONDOR_JOB_AD', str(ad))
    assert run_task(campaign, 'one') == 0
    provenance = json.loads((campaign / 'one_attempt_001' / 'provenance.json').read_text())
    assert 'scheduler' not in provenance


def test_bundled_worker_records_scheduler_even_on_input_guard_failure(tmp_path, monkeypatch):
    import subprocess
    import sys
    from yall_run.condor_backend import render_condor
    spec = tmp_path / 'Yallfile'
    spec.write_text('campaign bundled\nbackend condor\none:\n'
                    '    @input missing absent.dat\n    /bin/true\n')
    campaign = render_condor(load_spec(spec), tmp_path / 'campaigns')
    ad = tmp_path / 'job.ad'
    ad.write_text('ClusterId = 50\nProcId = 0\nYallDAGRetry = 1\n')
    monkeypatch.setenv('_CONDOR_JOB_AD', str(ad))
    result = subprocess.run([sys.executable, '-I', str(campaign / 'condor' / 'yall_worker.py'),
                             str(campaign), 'one'], capture_output=True, text=True)
    assert result.returncode == 2
    provenance = json.loads((campaign / 'one_attempt_001' / 'provenance.json').read_text())
    assert provenance['scheduler']['job_id'] == '50.0'
    attempt = json.loads((campaign / 'one_attempt_001' / 'attempt.json').read_text())
    assert attempt['failure']['kind'] == 'missing_inputs'
