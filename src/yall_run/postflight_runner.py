from __future__ import annotations

import json
from pathlib import Path


def write_runner(path: str | Path, *, campaign_dir: Path, workflow_dir: Path,
                 commands: list[object]) -> None:
    """Write a self-contained postflight runner for scheduler-side execution."""
    payload = json.dumps(commands, separators=(",", ":"))
    source = f'''#!/usr/bin/env python3
import datetime, json, os, pathlib, subprocess, sys

campaign = pathlib.Path({str(campaign_dir)!r})
workflow = pathlib.Path({str(workflow_dir)!r})
commands = json.loads({payload!r})
root = campaign / "postflight"
root.mkdir(parents=True, exist_ok=True)
records = []
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
for index, command in enumerate(commands, 1):
    directory = root / f"{{index:03d}}"
    directory.mkdir(parents=True, exist_ok=True)
    stdout = directory / "stdout.log"
    stderr = directory / "stderr.log"
    argv = ["/bin/bash", "-lc", command] if isinstance(command, str) else list(command)
    env = os.environ.copy()
    env.update(YALL_CAMPAIGN_DIR=str(campaign), YALL_CAMPAIGN_ID=campaign.name,
               YALL_WORKFLOW_DIR=str(workflow))
    record = {{"hook": "postflight", "index": index, "command": command,
               "cwd": str(campaign), "started_at": now()}}
    try:
        with stdout.open("w") as out, stderr.open("w") as err:
            completed = subprocess.run(argv, cwd=campaign, env=env,
                                       stdout=out, stderr=err, text=True)
        record["returncode"] = completed.returncode
        record["state"] = "completed" if completed.returncode == 0 else "failed"
    except Exception as exc:
        record["state"] = "failed"
        record["error"] = str(exc)
    record["finished_at"] = now()
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\\n")
    records.append(record)
    if record["state"] != "completed":
        sys.exit(1)
(campaign / "postflight.json").write_text(json.dumps(
    {{"started_at": records[0]["started_at"] if records else now(),
     "finished_at": records[-1]["finished_at"] if records else now(),
     "state": "completed", "commands": records}}, indent=2) + "\\n")
'''
    path = Path(path)
    path.write_text(source)
    path.chmod(0o755)
