import json
import pytest

from yall_run.cli import main
from yall_run.model import CampaignSpec, load_spec


def write_spec(tmp_path, directives=""):
    source = tmp_path / "Yallfile"
    source.write_text(f"campaign setup\n{directives}\nwork:\n    echo work\n")
    return source


def test_preflight_is_optional_and_does_not_add_tasks(tmp_path):
    source = write_spec(tmp_path)
    spec = load_spec(source)
    assert spec.preflight == ()
    assert CampaignSpec(name="empty", tasks=(), source=source).preflight == ()
    assert [task.name for task in spec.tasks] == ["work"]


def test_postflight_is_optional_and_does_not_add_tasks(tmp_path):
    source = write_spec(tmp_path, "%postflight echo done")
    spec = load_spec(source)
    assert spec.postflight == (("echo", "done"),)
    assert [task.name for task in spec.tasks] == ["work"]


def test_preflight_preserves_order_and_freezes_argv_before_substitution(tmp_path, monkeypatch):
    value = "/work/has spaces/'quotes'/$(literal);still-data"
    monkeypatch.setenv("SETUP_ROOT", value)
    source = write_spec(tmp_path, """@env SETUP_ROOT
@set RESULT {BASE}/result
@set BASE {SETUP_ROOT}
%preflight python3 prepare_host.py --output {RESULT}
%preflight /bin/test -d "{SETUP_ROOT}"
%preflight printf '%s' ""
""")
    spec = load_spec(source)
    monkeypatch.setenv("SETUP_ROOT", "/changed")
    assert spec.preflight == (
        ("python3", "prepare_host.py", "--output", value + "/result"),
        ("/bin/test", "-d", value),
        ("printf", "%s", ""),
    )
    assert [task.command for task in spec.tasks] == [("echo", "work")]


def test_explicit_shell_preflight_preserves_shell_and_quoted_placeholders(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUP_ROOT", "/work/has spaces")
    source = write_spec(tmp_path, '''@env SETUP_ROOT
%preflight ! test -d "{SETUP_ROOT}" && \\
    printf '%s\\n' "{SETUP_ROOT}" > 'setup log.txt'
''')
    assert load_spec(source).preflight == (
        "test -d \"/work/has spaces\" && printf '%s\\n' \"/work/has spaces\" > 'setup log.txt'",
    )


def test_explicit_shell_postflight_is_parsed(tmp_path):
    source = write_spec(tmp_path, "%postflight ! printf '%s' done > postflight.txt")
    assert load_spec(source).postflight == ("printf '%s' done > postflight.txt",)


@pytest.mark.parametrize("directive, error", [
    ("%preflight", "nonempty command"),
    ("%preflight !", "nonempty command"),
    ('%preflight ""', "nonempty command"),
    ('@set EMPTY ""\n%preflight {EMPTY}', "nonempty command"),
    ("%preflight echo {MISSING}", "no value for"),
    ("@set BASE {MISSING}\n%preflight echo {BASE}", "no value for"),
    ("%preflight ! echo {MISSING}", "no value for"),
    ("%preflight echo \0", "NUL"),
    ("%preflight ! echo \0", "NUL"),
    ("%preflight echo @inputs", "task inputs or outputs"),
    ("%preflight echo @output.result", "task inputs or outputs"),
    ("%preflight ! echo @input.data", "task inputs or outputs"),
    ("%preflight ! echo @outputs", "task inputs or outputs"),
    ('%preflight echo "unterminated', "No closing quotation"),
])
def test_invalid_preflight_is_rejected(tmp_path, directive, error):
    with pytest.raises(ValueError, match=error):
        load_spec(write_spec(tmp_path, directive))


def test_preflight_rejects_missing_imported_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("YALL_TEST_MISSING_ENV", raising=False)
    source = write_spec(tmp_path, "@env YALL_TEST_MISSING_ENV\n%preflight echo {YALL_TEST_MISSING_ENV}")
    with pytest.raises(ValueError, match="required environment variable"):
        load_spec(source)


@pytest.mark.parametrize("suffix, error", [
    ("%preflight echo late\n", "must appear before tasks"),
    ("    %preflight echo nested\n", "task directive %preflight"),
])
def test_preflight_must_be_campaign_level_before_tasks(tmp_path, suffix, error):
    source = write_spec(tmp_path)
    source.write_text(source.read_text() + suffix)
    with pytest.raises(ValueError, match=error):
        load_spec(source)


def test_validate_and_all_plans_show_preflight_without_execution(tmp_path, monkeypatch, capsys):
    source = write_spec(tmp_path, "%preflight ! printf ran > marker\n%preflight echo 'with spaces'")
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)

    assert main(["validate", str(source)]) == 0
    assert "1 tasks" in capsys.readouterr().out
    assert main(["plan", str(source)]) == 0
    output = capsys.readouterr().out
    assert f"Host preflight (during create, cwd={tmp_path}):" in output
    assert "1: ! printf ran > marker" in output
    assert "2: echo 'with spaces'" in output
    assert "work: echo work" in output

    assert main(["plan", str(source), "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["preflight"] == [
        {"command": "printf ran > marker", "cwd": str(tmp_path)},
        {"command": ["echo", "with spaces"], "cwd": str(tmp_path)},
    ]
    assert [task["name"] for task in plan["tasks"]] == ["work"]
    assert main(["plan", str(source), "--dot"]) == 0
    dot = capsys.readouterr().out
    assert '"work";' in dot
    assert "preflight" not in dot
    assert "marker" not in dot
    assert not (tmp_path / "marker").exists()
    assert not (caller / "marker").exists()
    assert not (caller / "campaigns").exists()
