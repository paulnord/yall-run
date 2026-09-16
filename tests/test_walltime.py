"""Wall time is portable input, not a backend-dependent duration string."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from yall_run.campaign import create_campaign, start_local
from yall_run.cli import main
from yall_run.condor_backend import render_condor
from yall_run.export import export_provenance, schema_sql
from yall_run.model import CondorSpec, ResourceSpec, load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.slurm_backend import render_slurm
from yall_run.walltime import (
    MAX_WALLTIME_SECONDS, effective_walltime, format_walltime,
    parse_walltime, validate_walltime,
)


@pytest.mark.parametrize('text, seconds', [
    ('1', 1), ('7200', 7200), ('7200s', 7200), ('120m', 7200),
    ('2h', 7200), ('2H', 7200), ('1d', 86400), ('1h30m', 5400),
    ('2d3h4m5s', 183845), ('1h0m1s', 3601), ('90m', 5400),
    ('02:00:00', 7200), ('25:00:00', 90000), ('1-01:00:00', 90000),
    ('00:00:01', 1), ('0-00:01:01', 61), ('  2h  ', 7200),
    (str(MAX_WALLTIME_SECONDS), MAX_WALLTIME_SECONDS),
])
def test_parse_walltime(text, seconds):
    assert parse_walltime(text) == seconds


@pytest.mark.parametrize('text', [
    '', '0', '0s', '0h0m', '-1', '-2h', '+2h', '1.5h', '0.1s',
    'infinite', 'unlimited', 'none', '2w', '1e3', '1h30', '30m1h',
    '1h2h', '2 h', '1:30', '00:60:00', '00:00:60', '1-24:00:00',
    '2h;echo bad', 'NaN', 'inf', '999999999999999h', '1'*65,
    str(MAX_WALLTIME_SECONDS + 1),
])
def test_parse_walltime_rejects_invalid_or_ambiguous_values(text):
    with pytest.raises(ValueError, match='wall time'):
        parse_walltime(text)


@pytest.mark.parametrize('value', [0, -1, 1.5, '7200', True, False, 2**31])
@pytest.mark.parametrize('model', ['task', 'default'])
def test_model_rejects_invalid_normalized_time(value, model):
    with pytest.raises(ValueError, match='wall time'):
        if model == 'task':
            ResourceSpec(walltime_seconds=value)
        else:
            CondorSpec(request_walltime_seconds=value)


def test_optional_values_and_task_precedence():
    assert validate_walltime(None) is None
    assert ResourceSpec().walltime_seconds is None
    assert CondorSpec().request_walltime_seconds is None
    assert effective_walltime(None, None) is None
    assert effective_walltime(None, 7200) == 7200
    assert effective_walltime(1800, 7200) == 1800
    with pytest.raises(ValueError):
        effective_walltime(0, 7200)


@pytest.mark.parametrize('seconds, clock, slurm', [
    (1, '00:00:01', '00:00:01'),
    (61, '00:01:01', '00:01:01'),
    (7200, '02:00:00', '02:00:00'),
    (86399, '23:59:59', '23:59:59'),
    (86400, '24:00:00', '1-00:00:00'),
    (90061, '25:01:01', '1-01:01:01'),
    (604800, '168:00:00', '7-00:00:00'),
])
def test_native_time_formats_do_not_wrap_at_midnight(seconds, clock, slurm):
    assert format_walltime(seconds) == clock
    assert format_walltime(seconds, slurm=True) == slurm


@pytest.mark.parametrize('scope', ['campaign', 'task'])
@pytest.mark.parametrize('value', ['0', '1:30', 'nonsense', '1.5h'])
def test_syntax_time_errors_include_directive_and_line(tmp_path, scope, value):
    spec = tmp_path / 'Yallfile'
    prefix = 'campaign invalid\n'
    body = f'%time {value}\nhello:\n    echo hello\n'
    if scope == 'task':
        body = f'hello:\n    %time {value}\n    echo hello\n'
    spec.write_text(prefix + body)
    with pytest.raises(ValueError, match=r'line [23]: %time:'):
        load_spec(spec)


@pytest.mark.parametrize('scope', ['campaign', 'task'])
@pytest.mark.parametrize('directive', ['%time', '%time 2h 30m'])
def test_syntax_time_requires_exactly_one_value(tmp_path, scope, directive):
    spec = tmp_path / 'Yallfile'
    body = f'{directive}\nhello:\n    echo hello\n'
    if scope == 'task':
        body = f'hello:\n    {directive}\n    echo hello\n'
    spec.write_text('campaign invalid\n' + body)
    with pytest.raises(ValueError, match='malformed.*%time'):
        load_spec(spec)


def _spec(tmp_path, backend='condor', default='2h', override='30m'):
    path = tmp_path / 'Yallfile'
    path.write_text(
        f'campaign timing\nbackend {backend}\n'
        + (f'%time {default}\n' if default else '')
        + '\nnormal:\n    echo normal\n\nshort: normal\n'
        + (f'    %time {override}\n' if override else '')
        + '    echo short\n'
    )
    return load_spec(path)


RENDERERS = [('condor', render_condor), ('slurm', render_slurm), ('pbs', render_pbs)]


def _native_file(campaign, backend, index, task):
    suffix = 'sub' if backend == 'condor' else 'sh'
    return campaign / backend / f'yall_{index:04d}_{task}.{suffix}'


def _request_line(backend, seconds):
    if backend == 'condor':
        return f'+MaxRuntime = {seconds}'
    if backend == 'slurm':
        return f'#SBATCH --time={format_walltime(seconds, slurm=True)}'
    return f'#PBS -l walltime={format_walltime(seconds)}'


@pytest.mark.parametrize('backend, renderer', RENDERERS)
def test_defaults_and_overrides_render_and_freeze(tmp_path, backend, renderer):
    spec = _spec(tmp_path, backend)
    assert spec.condor.request_walltime_seconds == 7200
    assert spec.tasks[0].resources.walltime_seconds is None
    assert spec.tasks[1].resources.walltime_seconds == 1800
    campaign = renderer(spec, tmp_path / 'campaigns')
    manifest = json.loads((campaign / 'campaign.json').read_text())
    for index, task, seconds in [(0, 'normal', 7200), (1, 'short', 1800)]:
        path = _native_file(campaign, backend, index, task)
        text = path.read_text()
        assert _request_line(backend, seconds) in text.splitlines()
        assert 'JobFlavour' not in text
        assert manifest['tasks'][task]['resources']['walltime_seconds'] == seconds
        if backend != 'condor':
            subprocess.run(['bash', '-n', str(path)], check=True)
    # Defaults remain recorded in the renderer metadata too.
    render = json.loads((campaign / backend / 'render.json').read_text())
    if backend == 'condor':
        assert render['condor']['request_walltime_seconds'] == 7200
    else:
        assert render['resources']['walltime_seconds'] == 7200


@pytest.mark.parametrize('backend, renderer', RENDERERS)
def test_absent_time_emits_no_scheduler_limit(tmp_path, backend, renderer):
    spec = _spec(tmp_path, backend, default=None, override=None)
    campaign = renderer(spec, tmp_path / 'campaigns')
    text = _native_file(campaign, backend, 0, 'normal').read_text()
    assert 'MaxRuntime' not in text
    assert '--time=' not in text
    assert '-l walltime=' not in text
    manifest = json.loads((campaign / 'campaign.json').read_text())
    assert manifest['tasks']['normal']['resources']['walltime_seconds'] is None


@pytest.mark.parametrize('backend, renderer', RENDERERS)
@pytest.mark.parametrize('duration, seconds', [('1s', 1), ('61s', 61), ('25h', 90000)])
def test_non_hour_durations_render_exact_requests(tmp_path, backend, renderer, duration, seconds):
    spec = _spec(tmp_path, backend, default=duration, override=None)
    campaign = renderer(spec, tmp_path / 'campaigns')
    text = _native_file(campaign, backend, 0, 'normal').read_text()
    assert _request_line(backend, seconds) in text.splitlines()


def test_task_only_time_does_not_leak_to_siblings(tmp_path):
    campaign = render_condor(_spec(tmp_path, default=None), tmp_path / 'campaigns')
    assert '+MaxRuntime' not in _native_file(campaign, 'condor', 0, 'normal').read_text()
    assert '+MaxRuntime = 1800' in _native_file(campaign, 'condor', 1, 'short').read_text()


def test_pattern_family_time_and_overrides(tmp_path):
    path = tmp_path / 'Yallfile'
    path.write_text(
        'campaign patterns\n%time 2h\n\n'
        'convert-{run}:\n    @each run 328 330\n'
        '    %time 10m\n    echo {run}\n\n'
        'analyze-{run}: convert-{run}\n    echo {run}\n'
    )
    campaign = create_campaign(load_spec(path), tmp_path / 'campaigns')
    tasks = json.loads((campaign / 'campaign.json').read_text())['tasks']
    assert [tasks[f'convert-{r}']['resources']['walltime_seconds'] for r in (328, 330)] == [600, 600]
    assert [tasks[f'analyze-{r}']['resources']['walltime_seconds'] for r in (328, 330)] == [7200, 7200]


def test_programmatic_default_is_frozen_without_parser(tmp_path):
    spec = _spec(tmp_path, default=None, override=None)
    spec = replace(spec, condor=replace(spec.condor, request_walltime_seconds=7200))
    campaign = create_campaign(spec, tmp_path / 'campaigns')
    manifest = json.loads((campaign / 'campaign.json').read_text())
    assert manifest['tasks']['normal']['resources']['walltime_seconds'] == 7200


def test_plan_displays_effective_time_and_json_seconds(tmp_path, monkeypatch, capsys):
    _spec(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(['plan']) == 0
    text = capsys.readouterr().out
    assert 'time=02:00:00' in text
    assert 'time=00:30:00' in text
    assert main(['plan', '--json']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [task['resources']['walltime_seconds'] for task in plan['tasks']] == [7200, 1800]


def test_invalid_time_does_not_create_campaign_or_submit(tmp_path, monkeypatch, capsys):
    (tmp_path / 'Yallfile').write_text('campaign bad\n%time 0\nhello:\n    echo hi\n')
    monkeypatch.chdir(tmp_path)
    assert main(['create']) == 2
    assert '%time' in capsys.readouterr().err
    assert not (tmp_path / 'campaigns').exists()


@pytest.mark.parametrize('backend, renderer', RENDERERS)
def test_later_recipe_edits_do_not_change_frozen_time(tmp_path, backend, renderer):
    spec = _spec(tmp_path, backend)
    campaign = renderer(spec, tmp_path / 'campaigns')
    paths = [campaign / 'campaign.json', campaign / 'Yallfile',
             _native_file(campaign, backend, 0, 'normal')]
    before = {p: p.read_bytes() for p in paths}
    spec.source.write_text(spec.source.read_text().replace('%time 2h', '%time 8h'))
    assert load_spec(spec.source).condor.request_walltime_seconds == 28800
    assert all(p.read_bytes() == content for p, content in before.items())


def test_local_records_walltime_without_claiming_scheduler_enforcement(tmp_path):
    campaign = create_campaign(_spec(tmp_path, 'local'), tmp_path / 'campaigns')
    start_local(campaign)
    record = json.loads((campaign / 'normal_attempt_001' / 'provenance.json').read_text())
    assert record['task']['resources']['walltime_seconds'] == 7200


def test_export_normalized_time_to_sqlite_sql_and_csv(tmp_path):
    campaign = create_campaign(_spec(tmp_path), tmp_path / 'campaigns')
    database, sql, csv_dir = tmp_path / 'results.sqlite', tmp_path / 'results.sql', tmp_path / 'csv'
    export_provenance([campaign], sqlite_path=database, sql_path=sql, csv_dir=csv_dir)
    expected = [('normal', 7200), ('short', 1800)]
    query = 'SELECT task_name, walltime_seconds FROM task ORDER BY task_index'
    with sqlite3.connect(database) as db:
        assert db.execute(query).fetchall() == expected
    with sqlite3.connect(':memory:') as db:
        db.executescript(sql.read_text())
        assert db.execute(query).fetchall() == expected
    with (csv_dir / 'task.csv').open() as handle:
        rows = list(csv.DictReader(handle))
    assert [(r['task_name'], int(r['walltime_seconds'])) for r in rows] == expected


def test_export_migrates_existing_database_and_preserves_old_rows(tmp_path):
    database = tmp_path / 'existing.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(schema_sql().replace('    walltime_seconds INTEGER,\n', ''))
        db.execute("INSERT INTO campaign (campaign_id, name, backend, campaign_dir) VALUES ('old', 'old', 'local', '/old')")
        db.execute("INSERT INTO task (campaign_id, task_name, task_index, retries, overwrite) VALUES ('old', 'old-task', 0, 0, 0)")
    campaign = create_campaign(_spec(tmp_path), tmp_path / 'campaigns')
    export_provenance([campaign], sqlite_path=database)
    export_provenance([campaign], sqlite_path=database)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT walltime_seconds FROM task WHERE campaign_id = 'old'").fetchone() == (None,)
        assert db.execute("SELECT walltime_seconds FROM task WHERE task_name = 'normal'").fetchone() == (7200,)


def test_legacy_campaign_without_time_still_exports(tmp_path):
    campaign = create_campaign(_spec(tmp_path, default=None, override=None), tmp_path / 'campaigns')
    path = campaign / 'campaign.json'
    manifest = json.loads(path.read_text())
    for task in manifest['tasks'].values():
        del task['resources']['walltime_seconds']
    path.write_text(json.dumps(manifest))
    database = tmp_path / 'legacy.sqlite'
    export_provenance([campaign], sqlite_path=database)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT walltime_seconds FROM task').fetchall() == [(None,), (None,)]


@pytest.mark.parametrize('backend, renderer', RENDERERS)
def test_resume_preserves_frozen_time_when_recovery_is_available(tmp_path, monkeypatch, backend, renderer):
    # This cross-feature check becomes active when the separate resume PR lands.
    recovery = pytest.importorskip('yall_run.recovery')
    from yall_run.campaign import begin_campaign
    from yall_run.worker import run_task
    from test_recovery import Scheduler, write

    spec = _spec(tmp_path, backend)
    spec = replace(spec, tasks=(spec.tasks[0], replace(spec.tasks[1], command=('/bin/false',))))
    campaign = renderer(spec, tmp_path / 'campaigns')
    begin_campaign(campaign)
    assert run_task(campaign, 'normal') == 0
    assert run_task(campaign, 'short') == 1
    record = {'returncode': 0}
    if backend == 'condor':
        record['cluster_id'] = 100
        (campaign / backend / 'campaign.dag.rescue001').write_text('DONE yall_0000_normal\n')
    else:
        suffix = '.server' if backend == 'pbs' else ''
        record['jobs'] = {'normal': '10' + suffix, 'short': '11' + suffix}
    write(campaign / backend / 'submit.json', record)
    original = _native_file(campaign, backend, 1, 'short').read_bytes()
    manifest = (campaign / 'campaign.json').read_bytes()
    spec.source.write_text(spec.source.read_text().replace('%time 30m', '%time 8h'))
    monkeypatch.setattr(recovery, '_run', Scheduler(backend))
    assert recovery.resume_campaign(campaign) == 0
    suffix = 'sub' if backend == 'condor' else 'sh'
    staged = campaign / 'resumes' / '0001' / backend / f'yall_0001_short.{suffix}'
    assert _request_line(backend, 1800) in staged.read_text().splitlines()
    assert _native_file(campaign, backend, 1, 'short').read_bytes() == original
    assert (campaign / 'campaign.json').read_bytes() == manifest
