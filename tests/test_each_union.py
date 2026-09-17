"""One conversion family can consume a stable union of scientific run lists."""
import json

import pytest

from yall_run.amend import amend_campaign
from yall_run.campaign import begin_campaign, create_campaign, start_local
from yall_run.cli import main
from yall_run.model import load_spec


def load(tmp_path, text):
    path = tmp_path / 'Yallfile'
    path.write_text('campaign union\n' + text)
    return load_spec(path)


WORKFLOW = '''@table pairs ped run:
    296 298
    296 300
    303 296
prepare:
    echo ready
convert-{run}: prepare
    @each run in pairs.ped pairs.run
    @output root converted-{run}.root
    ! printf converted > @output.root
pedestal-{ped}: convert-{ped}
    @each ped in pairs.ped
    @input root converted-{ped}.root
    @output fit pedestal-{ped}.txt
    ! printf fitted > @output.fit
transfer-{ped}-{run}: pedestal-{ped} convert-{run}
    @each ped run in pairs
    @input fit pedestal-{ped}.txt
    @input root converted-{run}.root
    @output root transferred-{run}.root
    ! printf transferred > @output.root
mip-{ped}-{run}: transfer-{ped}-{run}
    @input root transferred-{run}.root
    echo done
'''


def test_shared_pedestal_and_cross_column_overlap(tmp_path):
    spec = load(tmp_path, WORKFLOW)
    tasks = {task.name: task for task in spec.tasks}
    assert [n for n in tasks if n.startswith('convert-')] == [
        'convert-296', 'convert-303', 'convert-298', 'convert-300']
    assert [n for n in tasks if n.startswith('pedestal-')] == ['pedestal-296', 'pedestal-303']
    assert tasks['pedestal-296'].parents == ('convert-296',)
    assert tasks['transfer-296-298'].parents == ('pedestal-296', 'convert-298')
    assert tasks['transfer-296-300'].parents == ('pedestal-296', 'convert-300')
    assert tasks['transfer-303-296'].parents == ('pedestal-303', 'convert-296')
    assert tasks['mip-296-300'].parents == ('transfer-296-300',)
    assert 'transfer-303-300' not in tasks  # No product.
    assert len(tasks) == 13


def test_union_of_lists_columns_and_repeated_sources_preserves_order(tmp_path):
    spec = load(tmp_path, '''@list extra 0308 0296
@table pairs ped run:
    0296 0298
    0303 0296
convert-{r}:
    @each r in extra pairs.ped \\
        pairs.run extra
    echo {r}
''')
    assert [t.command for t in spec.tasks] == [
        ('echo', '0308'), ('echo', '0296'), ('echo', '0303'), ('echo', '0298')]


def test_complete_table_union_deduplicates_rows_not_cells(tmp_path):
    spec = load(tmp_path, '''@table first ped run:
    296 298
    296 300
@table second p m:
    296 300
    303 304
cal-{p}-{m}:
    @each p m in first second
    echo {p} {m}
''')
    assert [t.name for t in spec.tasks] == ['cal-296-298', 'cal-296-300', 'cal-303-304']
    assert spec.tasks[1].command == ('echo', '296', '300')


def test_quoted_values_static_substitution_and_string_spelling(tmp_path, monkeypatch):
    monkeypatch.setenv('RUN', '00296')
    spec = load(tmp_path, '''@env RUN
@list first "a sample.dat" {RUN}
@list second "a sample.dat" 296
x-{file}:
    @each file in first second
    @input raw {file}
    cat @input.raw
''')
    assert [t.command for t in spec.tasks] == [
        ('cat', 'a sample.dat'), ('cat', '00296'), ('cat', '296')]
    literal = load(tmp_path, '@list a "*.dat"\n@list b "[abc].dat" "*.dat"\n'
                   'x-{p}:\n    @each p in a b\n    echo {p}\n')
    assert [t.command for t in literal.tasks] == [('echo', '*.dat'), ('echo', '[abc].dat')]
    assert all(not t.inputs for t in literal.tasks)


@pytest.mark.parametrize('sources,match', [
    ('pairs.ped missing', "unknown parameter set 'missing'"),
    ('pairs.ped pairs.missing', 'unknown column'),
    ('runs pairs', "'pairs' provides 2 field"),
    ('pairs runs', "'pairs' provides 2 field"),
    ('runs pairs.ped.extra', 'invalid named'),
    ('pairs.ped runs.value', 'has no columns'),
    ('', 'at least one source'),
])
def test_every_source_is_validated(tmp_path, sources, match):
    with pytest.raises(ValueError, match=match):
        load(tmp_path, '@list runs 296\n@table pairs ped run:\n    296 298\n'
             f'x-{{r}}:\n    @each r in {sources}\n    echo {{r}}\n')


@pytest.mark.parametrize('declaration', [
    '@list a 1 1',
    '@set X 1\n@list a 1 {X}',
    '@table a x:\n    1\n    1',
])
def test_duplicate_declaration_not_hidden_by_union(tmp_path, declaration):
    with pytest.raises(ValueError, match='rows must be unique'):
        load(tmp_path, declaration + '\n@list b 2\nx-{r}:\n    @each r in a b\n    echo {r}\n')


def test_in_identifiers_remain_unambiguous(tmp_path):
    spec = load(tmp_path, '@list in 1 2\n@list other 2 3\n'
                 'x-{out}:\n    @each out in in other\n    echo {out}\n')
    assert [t.name for t in spec.tasks] == ['x-1', 'x-2', 'x-3']
    spec = load(tmp_path, '@table in a b:\n    1 2\n@table more a b:\n    3 4\n'
                 'x-{out}-{in}:\n    @each out in in in more\n    echo {out} {in}\n')
    assert [t.command for t in spec.tasks] == [('echo', '1', '2'), ('echo', '3', '4')]


def test_output_ownership_not_bypassed(tmp_path):
    with pytest.raises(ValueError, match='owned by both'):
        load(tmp_path, '@list a 1 2\n@list b 2 3\nx-{r}:\n    @each r in a b\n'
             '    @output root same.root\n    echo {r}\n')


@pytest.mark.parametrize('backend', ['local', 'condor', 'slurm', 'pbs'])
def test_union_freezes_for_every_backend(tmp_path, backend):
    from yall_run.condor_backend import render_condor
    from yall_run.slurm_backend import render_slurm
    from yall_run.pbs_backend import render_pbs
    spec = load(tmp_path, f'backend {backend}\n' + WORKFLOW)
    render = {'local': create_campaign, 'condor': render_condor,
              'slurm': render_slurm, 'pbs': render_pbs}[backend]
    campaign = render(spec, tmp_path / 'campaigns')
    manifest = json.loads((campaign / 'campaign.json').read_text())
    assert len(manifest['tasks']) == 13
    assert manifest['tasks']['transfer-296-300']['parents'] == ['pedestal-296', 'convert-300']
    assert manifest['tasks']['pedestal-296']['parents'] == ['convert-296']
    assert manifest['task_order'].count('convert-296') == 1
    assert '@each run in pairs.ped pairs.run' in (campaign / 'Yallfile').read_text()
    if backend == 'condor':
        render_record = json.loads((campaign / 'condor/render.json').read_text())
        nodes = render_record['node_names']
        dag = (campaign / 'condor/campaign.dag').read_text()
        assert f"PARENT {nodes['pedestal-296']} {nodes['convert-300']} CHILD {nodes['transfer-296-300']}" in dag


def test_union_to_explicit_refactor_is_not_amendment(tmp_path, monkeypatch):
    monkeypatch.setenv('RUN', '296')
    spec = load(tmp_path, '@env RUN\n@list a {RUN} 298\n@list b 298 300\n'
                 'x-{r}:\n    @each r in a b\n    echo {r}\n')
    campaign = create_campaign(spec, tmp_path / 'campaigns')
    begin_campaign(campaign)
    before = (campaign / 'campaign.json').read_bytes()
    spec.source.write_text('campaign union\n@env RUN\nx-{r}:\n'
                           '    @each r: {RUN} 298 300\n    echo {r}\n')
    monkeypatch.setenv('RUN', '999')
    assert amend_campaign(campaign, dry_run=True)['changes'] == []
    assert (campaign / 'campaign.json').read_bytes() == before


def test_frozen_union_runs_without_reloading_edited_sources(tmp_path):
    spec = load(tmp_path, WORKFLOW)
    campaign = create_campaign(spec, tmp_path / 'campaigns', local_jobs=3)
    before = (campaign / 'campaign.json').read_bytes()
    spec.source.write_text('not a Yallfile anymore\n')
    start_local(campaign)
    for name in ('296-298', '296-300', '303-296'):
        state = json.loads((campaign / 'state' / f'mip-{name}.json').read_text())
        assert state['state'] == 'completed'
    assert (campaign / 'campaign.json').read_bytes() == before


def test_cli_validation_and_plan_union(tmp_path, monkeypatch, capsys):
    load(tmp_path, WORKFLOW)
    monkeypatch.chdir(tmp_path)
    assert main(['validate']) == 0
    assert main(['plan']) == 0
    assert 'convert-296' in capsys.readouterr().out
