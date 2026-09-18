"""Partial explicit @each bindings inherit remaining fields from parents."""

import pytest

from yall_run.model import load_spec


def _load(tmp_path, text):
    source = tmp_path / "Yallfile"
    source.write_text("campaign partial-each\n" + text)
    return load_spec(source)


def test_partial_literal_each_inherits_field_from_parent(tmp_path):
    spec = _load(
        tmp_path,
        """parent-{run}:
    @each run: 484 486
    echo parent {run}

child-{type}-{run}: parent-{run}
    @each type pedestal
    echo child {type} {run}
""",
    )

    tasks = {task.name: task for task in spec.tasks}
    assert list(tasks) == [
        "parent-484",
        "parent-486",
        "child-pedestal-484",
        "child-pedestal-486",
    ]
    assert tasks["child-pedestal-484"].parents == ("parent-484",)
    assert tasks["child-pedestal-486"].command == (
        "echo",
        "child",
        "pedestal",
        "486",
    )


def test_partial_literal_each_filters_compatible_parent_rows(tmp_path):
    spec = _load(
        tmp_path,
        """parent-{type}-{run}:
    @each type run: pedestal 485 muon 484 muon 486
    echo parent {type} {run}

child-{type}-{run}: parent-{type}-{run}
    @each type pedestal
    echo child {type} {run}
""",
    )

    children = [task for task in spec.tasks if task.name.startswith("child-")]
    assert [task.name for task in children] == ["child-pedestal-485"]
    assert children[0].parents == ("parent-pedestal-485",)
    assert children[0].command == ("echo", "child", "pedestal", "485")


def test_partial_binding_filters_parent_fanin_and_unresolved_input(tmp_path):
    spec = _load(
        tmp_path,
        """@table runs type run:
    pedestal 485
    muon 484
    muon 486

convert-{type}-{run}:
    @each type run in runs
    @output root work/rawHGCROC_{run}.root
    convert {type} {run} @output.root

merge-{type}: convert-{type}-{run}
    @each type muon
    @input parts work/rawHGCROC_{run}.root
    hadd -f merged-{type}.root @input.parts
""",
    )

    merges = [task for task in spec.tasks if task.name.startswith("merge-")]
    assert len(merges) == 1
    merge = merges[0]
    assert merge.name == "merge-muon"
    assert merge.parents == ("convert-muon-484", "convert-muon-486")
    assert [item.path for item in merge.inputs] == [
        "work/rawHGCROC_484.root",
        "work/rawHGCROC_486.root",
    ]
    assert merge.command == (
        "hadd",
        "-f",
        "merged-muon.root",
        "work/rawHGCROC_484.root",
        "work/rawHGCROC_486.root",
    )


def test_partial_each_rejects_value_with_no_compatible_parent_rows(tmp_path):
    with pytest.raises(ValueError, match="no compatible rows"):
        _load(
            tmp_path,
            """parent-{type}-{run}:
    @each type run: muon 484 muon 486
    echo parent {type} {run}

child-{type}-{run}: parent-{type}-{run}
    @each type pedestal
    echo child {type} {run}
""",
        )


def test_partial_each_rejects_disagreeing_parents_after_filtering(tmp_path):
    with pytest.raises(ValueError, match="patterned parents disagree on values"):
        _load(
            tmp_path,
            """left-{type}-{run}:
    @each type run: pedestal 485 muon 484
    echo left {type} {run}

right-{type}-{run}:
    @each type run: pedestal 486 muon 484
    echo right {type} {run}

child-{type}-{run}: left-{type}-{run} right-{type}-{run}
    @each type pedestal
    echo child {type} {run}
""",
        )


def test_partial_each_compares_only_filtered_parent_subsets(tmp_path):
    spec = _load(
        tmp_path,
        """left-{type}-{run}:
    @each type run: pedestal 485 pedestal 486 muon 484
    echo left {type} {run}

right-{type}-{run}:
    @each type run: pedestal 485 pedestal 486 muon 999
    echo right {type} {run}

child-{type}-{run}: left-{type}-{run} right-{type}-{run}
    @each type pedestal
    echo child {type} {run}
""",
    )

    tasks = {task.name: task for task in spec.tasks}
    assert [name for name in tasks if name.startswith("child-")] == [
        "child-pedestal-485",
        "child-pedestal-486",
    ]
    assert tasks["child-pedestal-485"].parents == (
        "left-pedestal-485",
        "right-pedestal-485",
    )


def test_partial_each_names_must_still_be_task_placeholders(tmp_path):
    with pytest.raises(ValueError, match="explicit @each names must match"):
        _load(
            tmp_path,
            """parent-{run}:
    @each run 485
    echo parent {run}

child-{type}-{run}: parent-{run}
    @each sample pedestal
    echo child {type} {run}
""",
        )


def test_fully_specified_explicit_each_is_unchanged(tmp_path):
    spec = _load(
        tmp_path,
        """thing-{type}-{run}:
    @each type run: pedestal 485 muon 484 muon 486
    echo {type} {run}
""",
    )

    assert [task.name for task in spec.tasks] == [
        "thing-pedestal-485",
        "thing-muon-484",
        "thing-muon-486",
    ]
    assert [task.command for task in spec.tasks] == [
        ("echo", "pedestal", "485"),
        ("echo", "muon", "484"),
        ("echo", "muon", "486"),
    ]


def test_duplicate_partial_each_rows_remain_invalid(tmp_path):
    with pytest.raises(ValueError, match="explicit @each rows must be unique"):
        _load(
            tmp_path,
            """parent-{run}:
    @each run 485
    echo parent {run}

child-{type}-{run}: parent-{run}
    @each type: pedestal pedestal
    echo child {type} {run}
""",
        )


def test_partial_each_accepts_named_list_and_table_column_sources(tmp_path):
    spec = _load(
        tmp_path,
        """@list pedestal_types pedestal
@table selected label type:
    keep muon

parent-{type}-{run}:
    @each type run: pedestal 485 muon 484 muon 486
    echo parent {type} {run}

from-list-{type}-{run}: parent-{type}-{run}
    @each type in pedestal_types
    echo list {type} {run}

from-table-{type}-{run}: parent-{type}-{run}
    @each type in selected.type
    echo table {type} {run}
""",
    )

    tasks = {task.name: task for task in spec.tasks}
    assert [name for name in tasks if name.startswith("from-list-")] == [
        "from-list-pedestal-485"
    ]
    assert [name for name in tasks if name.startswith("from-table-")] == [
        "from-table-muon-484",
        "from-table-muon-486",
    ]
    assert tasks["from-list-pedestal-485"].parents == ("parent-pedestal-485",)
    assert tasks["from-table-muon-486"].parents == ("parent-muon-486",)
