import csv
import json
from pathlib import Path
import shlex
import sqlite3
import sys

from yall_run.campaign import create_campaign, start_local
from yall_run.cli import main as cli_main
from yall_run.export import export_provenance, schema_sql
from yall_run.model import load_spec


def _campaign(tmp_path: Path) -> Path:
    source = tmp_path / "input.dat"
    source.write_text("hello\n")
    script = tmp_path / "copy.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[2]).write_text(Path(sys.argv[1]).read_text().upper())\n"
    )
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign export-test\n"
        "@set run 308\n"
        "@set sample \"muon data\"\n\n"
        "copy:\n"
        "    @input source input.dat\n"
        "    @output result output.dat\n"
        "    %cpus 2\n"
        f"    {shlex.quote(sys.executable)} {shlex.quote(str(script))} "
        "@input.source @output.result\n\n"
        "check: copy\n"
        "    @input result output.dat\n"
        "    echo @input.result\n"
    )
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local", local_jobs=2
    )
    start_local(campaign_dir)
    return campaign_dir


def _pk_columns(db: sqlite3.Connection, table: str) -> list[str]:
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in sorted(rows, key=lambda row: row[5]) if row[5]]


def test_schema_primary_keys_follow_campaign_task_attempt_identity():
    with sqlite3.connect(":memory:") as db:
        db.executescript(schema_sql())
        assert _pk_columns(db, "campaign") == ["campaign_id"]
        assert _pk_columns(db, "task") == ["campaign_id", "task_name"]
        assert _pk_columns(db, "attempt") == ["campaign_id", "task_name", "attempt"]
        assert _pk_columns(db, "task_input") == [
            "campaign_id", "task_name", "input_index"
        ]
        assert _pk_columns(db, "attempt_output") == [
            "campaign_id", "task_name", "attempt", "output_index"
        ]


def test_export_writes_queryable_sqlite_and_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    campaign_dir = _campaign(tmp_path)
    sqlite_path = tmp_path / "yall.sqlite"
    csv_dir = tmp_path / "csv"

    campaigns, counts = export_provenance(
        [campaign_dir], sqlite_path=sqlite_path, csv_dir=csv_dir
    )
    assert campaigns == [campaign_dir]
    assert counts["campaign"] == 1
    assert counts["campaign_set"] == 2
    assert counts["task"] == 2
    assert counts["task_parent"] == 1
    assert counts["attempt"] == 2

    with sqlite3.connect(sqlite_path) as db:
        campaign_id = campaign_dir.name
        assert db.execute(
            "SELECT name, backend FROM campaign WHERE campaign_id = ?", (campaign_id,)
        ).fetchone() == ("export-test", "local")
        assert db.execute(
            "SELECT name, value FROM campaign_set WHERE campaign_id = ? ORDER BY name",
            (campaign_id,),
        ).fetchall() == [("run", "308"), ("sample", "muon data")]
        assert db.execute(
            "SELECT task_name, task_index FROM task WHERE campaign_id = ? ORDER BY task_index",
            (campaign_id,),
        ).fetchall() == [("copy", 0), ("check", 1)]
        assert db.execute(
            "SELECT parent_task_name FROM task_parent "
            "WHERE campaign_id = ? AND task_name = 'check'",
            (campaign_id,),
        ).fetchone() == ("copy",)
        assert db.execute(
            "SELECT role, path, creation_sha256 FROM task_input "
            "WHERE campaign_id = ? AND task_name = 'copy'",
            (campaign_id,),
        ).fetchone()[0] == "source"
        assert db.execute(
            "SELECT state, attempt FROM attempt WHERE campaign_id = ? ORDER BY task_name",
            (campaign_id,),
        ).fetchall() == [("completed", 1), ("completed", 1)]
        assert db.execute(
            "SELECT hostname FROM attempt_provenance WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()[0]

    with (csv_dir / "task.csv").open(newline="") as handle:
        task_rows = list(csv.DictReader(handle))
    assert [row["task_name"] for row in task_rows] == ["copy", "check"]
    assert (csv_dir / "attempt.csv").is_file()
    assert (csv_dir / "resume.csv").is_file()


def test_sql_dump_recreates_same_core_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    campaign_dir = _campaign(tmp_path)
    sql_path = tmp_path / "yall.sql"
    export_provenance([campaign_dir], sql_path=sql_path)

    with sqlite3.connect(":memory:") as db:
        db.executescript(sql_path.read_text())
        assert db.execute("SELECT COUNT(*) FROM campaign").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM task").fetchone() == (2,)
        assert db.execute("SELECT COUNT(*) FROM attempt").fetchone() == (2,)


def test_export_discovers_multiple_campaigns_under_directory(tmp_path):
    campaigns_root = tmp_path / "campaigns"
    for number in (1, 2):
        campaign_dir = campaigns_root / f"manual-{number}"
        (campaign_dir / "state").mkdir(parents=True)
        (campaign_dir / "campaign.json").write_text(json.dumps({
            "schema": 7,
            "id": f"manual-{number}",
            "name": f"manual {number}",
            "backend": "local",
            "tasks": {},
            "task_order": [],
        }))

    sqlite_path = tmp_path / "many.sqlite"
    campaigns, _ = export_provenance([campaigns_root], sqlite_path=sqlite_path)
    assert [path.name for path in campaigns] == ["manual-1", "manual-2"]
    with sqlite3.connect(sqlite_path) as db:
        assert db.execute("SELECT campaign_id FROM campaign ORDER BY campaign_id").fetchall() == [
            ("manual-1",), ("manual-2",)
        ]


def test_export_legacy_task_without_command(tmp_path):
    campaign_dir = tmp_path / "legacy"
    campaign_dir.mkdir()
    (campaign_dir / "campaign.json").write_text(json.dumps({
        "schema": 1,
        "id": "legacy-campaign",
        "name": "legacy",
        "backend": "local",
        "tasks": {
            "old-task": {
                "parents": [],
                "inputs": [],
                "outputs": [],
            }
        },
        "task_order": ["old-task"],
    }))

    sqlite_path = tmp_path / "legacy.sqlite"
    export_provenance([campaign_dir], sqlite_path=sqlite_path)

    with sqlite3.connect(sqlite_path) as db:
        assert db.execute(
            "SELECT task_name, command_json FROM task WHERE campaign_id = 'legacy-campaign'"
        ).fetchone() == ("old-task", None)


def test_cli_export_requires_output_and_reports_counts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    campaign_dir = _campaign(tmp_path)

    assert cli_main(["export", str(campaign_dir)]) == 2
    assert "at least one of --sqlite, --sql, or --csv-dir" in capsys.readouterr().err

    assert cli_main([
        "export", str(campaign_dir), "--sqlite", "export.sqlite", "--csv-dir", "csv"
    ]) == 0
    output = capsys.readouterr().out
    assert "exported 1 campaign(s)" in output
    assert "campaign=1" in output
    assert (tmp_path / "export.sqlite").is_file()
    assert (tmp_path / "csv" / "campaign.csv").is_file()
