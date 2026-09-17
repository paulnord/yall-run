"""Named recipe data must lower to the same graph as explicit @each rows."""
import json
from pathlib import Path

import pytest

from yall_run.amend import amend_campaign
from yall_run.campaign import begin_campaign, create_campaign
from yall_run.cli import main
from yall_run.model import load_spec
from yall_run.worker import run_task


def _load(tmp_path, text):
    path = tmp_path / 'Yallfile'
    path.write_text('campaign parameters\n' + text)
    return load_spec(path)


def test_named_list_reuse_continuations_and_order(tmp_path):
    spec = _load(tmp_path, '''@list runs 0308 \\
    0296 0298
convert-{run}:
    @each run in runs
    echo {run}
inspect-{run}: convert-{run}
    @each run in runs
    echo inspected {run}
''')
    assert [t.name for t in spec.tasks] == [
        'convert-0308', 'convert-0296', 'convert-0298',
        'inspect-0308', 'inspect-0296', 'inspect-0298']
    assert spec.tasks[0].command == ('echo', '0308')
    assert spec.tasks[-1].parents == ('convert-0298',)


def test_table_correlated_rows_columns_deduplicate_and_children_inherit(tmp_path):
    spec = _load(tmp_path, '''@table pairs ped run:
    299 300
    # One pedestal serves two muon runs.
    296 298

    296 304
convert-ped-{ped}:
    @each ped in pairs.ped
    echo {ped}
convert-muon-{run}:
    @each run in pairs.run
    echo {run}
transfer-{ped}-{run}: convert-ped-{ped} convert-muon-{run}
    @each ped run in pairs
    echo {ped} {run}
mip-{ped}-{run}: transfer-{ped}-{run}
    echo fit {ped} {run}
all: mip-{ped}-{run}
    echo done
''')
    tasks = {t.name: t for t in spec.tasks}
    assert list(tasks)[:5] == ['convert-ped-299', 'convert-ped-296',
                              'convert-muon-300', 'convert-muon-298', 'convert-muon-304']
    assert tasks['transfer-296-304'].parents == ('convert-ped-296', 'convert-muon-304')
    assert tasks['mip-296-304'].parents == ('transfer-296-304',)
    assert len(tasks['all'].parents) == 3
    assert 'transfer-299-298' not in tasks  # No Cartesian product.


def test_bind_table_columns_positionally(tmp_path):
    spec = _load(tmp_path, '''@table pairs pedestal muon:
    296 298
one-{p}-{m}:
    @each p m in pairs
    echo {p} {m}
''')
    assert spec.tasks[0].command == ('echo', '296', '298')


def test_table_row_width_is_checked_per_row_not_total(tmp_path):
    # Four values overall would fit two rows, but the individual rows are wrong.
    with pytest.raises(ValueError, match='line 3: @table row needs 2 values, got 1'):
        _load(tmp_path, '@table pairs ped run:\n    296\n    298 299 300\n'
              'task:\n    true\n')


def test_named_data_respects_static_values_and_quoted_files(tmp_path, monkeypatch):
    monkeypatch.setenv('FIRST', '0296')
    spec = _load(tmp_path, '''@env FIRST
@set SECOND 0308
@list runs {SECOND} {FIRST}
@list files "first sample.dat" "second sample.dat"
@table pairs ped run:
    {FIRST} 0298
job-{run}:
    @each run in runs
    echo {run}
copy-{file}:
    @each file in files
    @input raw {file}
    cat @input.raw
cal-{ped}-{run}:
    @each ped run in pairs
    echo {ped} {run}
''')
    assert spec.tasks[0].command == ('echo', '0308')
    assert spec.tasks[2].command == ('cat', 'first sample.dat')
    assert spec.tasks[-1].command == ('echo', '0296', '0298')
    assert dict(spec.set_values) == {'FIRST': '0296', 'SECOND': '0308'}


@pytest.mark.parametrize('declaration,task,match', [
    ('@list runs', 'x:\n    true', '@list needs'),
    ('@list bad-name 1', 'x:\n    true', 'invalid parameter set name'),
    ('@table pairs ped run', 'x:\n    true', "header must end"),
    ('@table pairs:', 'x:\n    true', '@table needs'),
    ('@table pairs ped ped:\n    1 2', 'x:\n    true', 'column names must be unique'),
    ('@table pairs ped bad-name:\n    1 2', 'x:\n    true', 'invalid @table column'),
    ('@table pairs ped run:', 'x:\n    true', 'must not be empty'),
    ('@table pairs ped run:\n    1 2\n    1 2', 'x:\n    true', 'rows must be unique'),
    ('@list runs 1 1', 'x:\n    true', 'rows must be unique'),
    ('@list runs ""', 'x:\n    true', 'empty or invalid'),
    ('@list runs {missing}', 'x:\n    true', 'no value for'),
    ('@set X 1\n@list runs 1 {X}', 'x:\n    true', 'rows must be unique'),
    ('@list runs 1\n@list runs 2', 'x:\n    true', 'duplicate parameter name'),
    ('@list runs 1\n@table runs x:\n    1', 'x:\n    true', 'duplicate parameter name'),
    ('@set runs 1\n@list runs 2', 'x:\n    true', 'duplicate parameter name'),
    ('@list runs 1\n@set runs 2', 'x:\n    true', 'duplicate parameter name'),
    ('@list runs 1', 'x-{r}:\n    @each r in absent\n    echo {r}', 'unknown parameter set'),
    ('@list runs 1', 'x-{r}:\n    @each r in runs.col\n    echo {r}', 'has no columns'),
    ('@table pairs ped run:\n    1 2', 'x-{r}:\n    @each r in pairs.foo\n    echo {r}', 'unknown column'),
    ('@table pairs ped run:\n    1 2', 'x-{r}:\n    @each r in pairs\n    echo {r}', 'provides 2 field'),
    ('@list runs 1', 'x-{p}-{r}:\n    @each p r in runs\n    echo {r}', 'provides 1 field'),
    ('@list runs 1', 'x-{r}:\n    @each r in\n    echo {r}', 'exactly one source'),
    ('@list runs 1', 'x-{r}:\n    @each r in runs other\n    echo {r}', 'exactly one source'),
    ('@list runs 1', 'x-{r}:\n    @each r in runs.bad.extra\n    echo {r}', 'invalid named'),
    ('@list runs 1', 'x-{r}:\n    @each p in runs\n    echo {r}', 'must match'),
])
def test_named_parameter_errors(tmp_path, declaration, task, match):
    with pytest.raises(ValueError, match=match):
        _load(tmp_path, declaration + '\n' + task + '\n')


def test_collection_cannot_shadow_imported_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('RUNS', '1')
    for text in ('@env RUNS\n@list RUNS 1', '@list RUNS 1\n@env RUNS'):
        with pytest.raises(ValueError, match='duplicate parameter name'):
            _load(tmp_path, text + '\nx:\n    true\n')


def test_declarations_belong_at_top_level_before_tasks(tmp_path):
    with pytest.raises(ValueError, match='must appear before tasks'):
        _load(tmp_path, 'x:\n    true\n@list runs 1\n')
    with pytest.raises(ValueError, match='unknown data directive @list'):
        _load(tmp_path, 'x:\n    @list runs 1\n    true\n')


def test_explicit_colon_form_preserves_literal_in_value(tmp_path):
    spec = _load(tmp_path, 'x-{mode}:\n    @each mode: in out\n    echo {mode}\n')
    assert [t.name for t in spec.tasks] == ['x-in', 'x-out']


def test_frozen_campaign_unchanged_after_list_edit(tmp_path):
    spec = _load(tmp_path, '@list runs 296 298\nx-{run}:\n    @each run in runs\n    echo {run}\n')
    campaign = create_campaign(spec, tmp_path / 'campaigns')
    begin_campaign(campaign)
    before = (campaign / 'campaign.json').read_bytes()
    spec.source.write_text(spec.source.read_text().replace('296 298', '299 300'))
    assert run_task(campaign, 'x-296') == 0
    assert (campaign / 'x-296_attempt_001' / 'stdout.log').read_text().strip() == '296'
    assert (campaign / 'campaign.json').read_bytes() == before
    assert '@list runs 296 298' in (campaign / 'Yallfile').read_text()
    with pytest.raises(ValueError):
        amend_campaign(campaign, dry_run=True)  # Changed task graph is not an amendment.


def test_inline_to_named_refactor_is_semantically_identical_for_amend(tmp_path, monkeypatch):
    monkeypatch.setenv('RUN', '296')
    spec = _load(tmp_path, '@env RUN\nx-{run}:\n    @each run: {RUN}\n    echo {run}\n')
    campaign = create_campaign(spec, tmp_path / 'campaigns')
    begin_campaign(campaign)
    spec.source.write_text('campaign parameters\n@env RUN\n@list runs {RUN}\n'
                           'x-{run}:\n    @each run in runs\n    echo {run}\n')
    monkeypatch.setenv('RUN', '999')
    assert amend_campaign(campaign, dry_run=True)['changes'] == []


def test_cli_create_and_start_named_parameters(tmp_path, monkeypatch, capsys):
    spec = _load(tmp_path, '@list runs 296 298\nx-{run}:\n    @each run in runs\n    echo {run}\n')
    monkeypatch.chdir(tmp_path)
    assert main(['validate']) == 0
    assert main(['plan']) == 0
    capsys.readouterr()
    assert main(['create', '--campaigns-dir', 'campaigns']) == 0
    campaign = Path(capsys.readouterr().out.strip())
    assert main(['start', str(campaign)]) == 0
    assert (campaign / 'x-298_attempt_001' / 'stdout.log').read_text().strip() == '298'


@pytest.mark.parametrize('backend', ['local', 'condor', 'slurm', 'pbs'])
def test_named_data_freezes_for_every_backend(tmp_path, backend):
    from yall_run.condor_backend import render_condor
    from yall_run.slurm_backend import render_slurm
    from yall_run.pbs_backend import render_pbs
    spec = _load(tmp_path, f'''backend {backend}
@table pairs left right:
    1 2
    3 4
x-{{a}}-{{b}}:
    @each a b in pairs
    echo {{a}} {{b}}
y-{{a}}-{{b}}: x-{{a}}-{{b}}
    echo done
''')
    render = {'local': create_campaign, 'condor': render_condor,
              'slurm': render_slurm, 'pbs': render_pbs}[backend]
    campaign = render(spec, tmp_path / 'campaigns')
    manifest = json.loads((campaign / 'campaign.json').read_text())
    assert manifest['task_order'] == ['x-1-2', 'x-3-4', 'y-1-2', 'y-3-4']
    assert manifest['tasks']['x-3-4']['command'] == ['echo', '3', '4']
    assert manifest['tasks']['y-3-4']['parents'] == ['x-3-4']
    assert '@table pairs' in (campaign / 'Yallfile').read_text()


def test_unused_declarations_do_not_expand_task_graph(tmp_path):
    spec = _load(tmp_path, '@list runs 1 2\n@table pairs a b:\n    3 4\n'
                 'plain:\n    echo plain\n')
    assert [t.name for t in spec.tasks] == ['plain']


def test_named_table_preserves_output_ownership_check(tmp_path):
    with pytest.raises(ValueError, match='owned by both'):
        _load(tmp_path, '@table pairs ped run:\n    1 2\n    1 3\n'
              'ped-{ped}-{run}:\n    @each ped run in pairs\n'
              '    @output root ped-{ped}.root\n    echo {ped}\n')


@pytest.mark.parametrize('text,line', [
    ('@list runs "unfinished\nx:\n    true\n', 2),
    ('@table pairs ped run:\n    1 "unfinished\nx:\n    true\n', 3),
])
def test_named_data_quote_errors_report_the_source_line(tmp_path, text, line):
    with pytest.raises(ValueError, match=f'line {line}: No closing quotation'):
        _load(tmp_path, text)


def test_in_can_be_a_binding_or_a_source_name(tmp_path):
    spec = _load(tmp_path, '@table in a b:\n    1 2\n'
                 'x-{out}-{in}:\n    @each out in in in\n    echo {out} {in}\n')
    assert spec.tasks[0].command == ('echo', '1', '2')


def test_named_values_are_literal_not_filesystem_discovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = _load(tmp_path, '@list patterns "*.dat" "[abc].dat"\n'
                 'show-{p}:\n    @each p in patterns\n    echo {p}\n')
    assert len(spec.tasks) == 2
    assert spec.tasks[0].command == ('echo', '*.dat')
    assert spec.tasks[1].command == ('echo', '[abc].dat')
    assert not spec.tasks[0].inputs


def test_named_parameters_example_runs(tmp_path):
    from yall_run.campaign import start_local
    source = Path(__file__).parents[1] / 'examples' / 'parameter-sets' / 'Yallfile'
    spec = load_spec(source)
    names = [task.name for task in spec.tasks]
    assert names.count('convert-ped-296') == 1
    assert 'calibrate-296-298' in names and 'calibrate-296-300' in names
    assert 'calibrate-303-298' not in names
    campaign = create_campaign(spec, tmp_path / 'campaigns')
    start_local(campaign)
    state = json.loads((campaign / 'state' / 'finish.json').read_text())
    assert state['state'] == 'completed'
