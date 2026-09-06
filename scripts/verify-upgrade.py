"""Check upgrade and rollback using two explicitly supplied installed interpreters."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-python", type=Path, required=True)
    parser.add_argument("--new-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old_python = args.old_python.absolute()
    new_python = args.new_python.absolute()
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}}
    with tempfile.TemporaryDirectory(prefix="gl-agent-lab-upgrade-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        env.update({"HOME": str(home), "USERPROFILE": str(home),
                    "LOCALAPPDATA": str(home), "APPDATA": str(home)})
        data = root / "original"
        restored = root / "rollback"
        archive = root / "before-upgrade.zip"

        def command(python: Path, directory: Path, *arguments: str, expected=0):
            process = subprocess.run(
                [str(python), "-I", "-m", "genlayer_agent_lab.cli", "--data-dir", str(directory), *arguments],
                cwd=root, env=env, capture_output=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if process.returncode != expected:
                raise RuntimeError(f"Upgrade verification failed at {arguments[0]}: exit {process.returncode}")
            return process.stdout.decode("utf-8").strip()

        old_version = command(old_python, data, "--version")
        new_version = command(new_python, data, "--version")
        reports = []
        for scenario, agent, code in (("escrow-normal", "safe", 0), ("escrow-provisional", "unsafe", 1)):
            run = json.loads(command(old_python, data, "run", scenario,
                                     "--agent", agent, "--backend", "fixture", expected=code))
            reports.append(json.loads(command(old_python, data, "report", run["run_id"])))
        backup = json.loads(command(new_python, data, "backup", "--output", str(archive)))
        for report in reports:
            actual = json.loads(command(new_python, data, "report", report["run_id"]))
            if actual != report:
                raise RuntimeError("Upgrade changed a completed historical report")
        command(new_python, data, "run", "treasury-normal", "--backend", "fixture")
        restore = json.loads(command(new_python, restored, "restore", str(archive)))
        for report in reports:
            actual = json.loads(command(old_python, restored, "report", report["run_id"]))
            if actual != report:
                raise RuntimeError("Old environment rollback changed a completed historical report")
        if len(json.loads(command(old_python, restored, "status"))) != len(reports):
            raise RuntimeError("Rollback did not restore the prior run set")
        original_token = (data / "admin.token").read_bytes()
        restored_token = (restored / "admin.token").read_bytes()
        if original_token == restored_token:
            raise RuntimeError("Restore failed to issue a fresh administrator credential")
        result = {"verification": "pass", "old_version": old_version, "new_version": new_version,
                  "historical_reports_unchanged": len(reports), "new_run_after_upgrade": True,
                  "rollback_prior_run_count": len(reports), "restored_admin_token_rotated": True,
                  "agent_tokens_revoked": restore["agent_tokens_revoked"],
                  "schema_version": backup["manifest"]["database"]["schema_version"],
                  "backend": "fixture", "studio_backup_included": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
