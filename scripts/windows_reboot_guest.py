"""One-shot Windows 11 guest observer; never requests a reboot.

Only the disposable, BIOS-marked LabUser VM may run this helper. The first
interactive logon installs and tests the actual wheel's current-user service.
The next OS boot is observed without installing or starting that service.
Durable phase claims prevent a retry from replacing failed first-boot evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FIXED_ROOT = Path("C:/LabTrial")
PORT = 8765
MAX_JSON = 131072


class TrialError(RuntimeError):
    """A fixed, public error code, never a raw provider/manager exception."""


def checked(condition, code):
    if not condition:
        raise TrialError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def value_hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load(path):
    checked(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_JSON,
            "invalid_trial_file")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    checked(type(value) is dict, "invalid_trial_file")
    return value


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("wb") as stream:
        stream.write(json.dumps(value, indent=2).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def claim(path, system):
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"boot_utc": system["boot_utc"]}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise TrialError("prior_attempt_incomplete") from None


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    checked(parsed.tzinfo is not None, "invalid_boot_timestamp")
    return parsed


def powershell(source):
    # This independent, bounded read-only query runs before importing Lab code.
    source = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; " + source
    encoded = base64.b64encode(source.encode("utf-16-le")).decode()
    binary = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    completed = subprocess.run([str(binary), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                               capture_output=True, timeout=20, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    checked(completed.returncode == 0 and len(completed.stdout) <= MAX_JSON,
            "guest_identity_query_failed")
    return json.loads(completed.stdout.decode("utf-8-sig"))


def system_info():
    return powershell(
        "$o=Get-CimInstance Win32_OperatingSystem; $b=Get-CimInstance Win32_BIOS; "
        "$p=Get-CimInstance Win32_ComputerSystemProduct; "
        "$w=[Security.Principal.WindowsIdentity]::GetCurrent(); "
        "$a=Get-CimInstance Win32_UserAccount -Filter \"LocalAccount=True AND Name='LabUser'\"; "
        "@{boot_utc=$o.LastBootUpTime.ToUniversalTime().ToString('o');"
        "observed_utc=[DateTime]::UtcNow.ToString('o');uptime_ms=[Environment]::TickCount64;"
        "caption=$o.Caption;version=$o.Version;product_type=[int]$o.ProductType;"
        "bios_serial=$b.SerialNumber;system_serial=$p.IdentifyingNumber;"
        "account=$w.Name;sid=$w.User.Value;local_sid=$a.SID;"
        "administrator=(@($w.Groups | ForEach-Object {$_.Value}) -contains 'S-1-5-32-544');"
        "session_id=(Get-Process -Id $PID).SessionId}|ConvertTo-Json -Compress")


def package_info():
    installed = importlib.metadata.distribution("genlayer-agent-lab")
    direct = json.loads(installed.read_text("direct_url.json") or "{}")
    archive = direct.get("archive_info", {})
    wheel_hash = archive.get("hashes", {}).get("sha256")
    if wheel_hash is None and archive.get("hash", "").startswith("sha256="):
        wheel_hash = archive["hash"][7:]
    return {"version": installed.version,
            "package_path": str(Path(installed.locate_file("genlayer_agent_lab")).resolve()),
            "executable": str(Path(sys.executable).resolve()), "wheel_sha256": wheel_hash,
            "record_sha256": hashlib.sha256((installed.read_text("RECORD") or "").encode()).hexdigest()}


def validate_identity(marker, nonce, identity, package, root):
    checked(bool(re.fullmatch(r"[0-9a-f]{32}", nonce)), "invalid_guest_id")
    checked(marker.get("nonce") == nonce and marker.get("bios_serial") == "GLAB-" + nonce
            and marker.get("user") == "LabUser", "guest_marker_mismatch")
    checked(marker["bios_serial"] in (identity.get("bios_serial"), identity.get("system_serial")),
            "guest_bios_mismatch")
    checked(identity.get("product_type") == 1 and "Windows 11" in identity.get("caption", ""),
            "windows_11_client_required")
    checked(identity.get("account", "").rsplit("\\", 1)[-1] == "LabUser"
            and isinstance(identity.get("sid"), str)
            and re.fullmatch(r"S-1-5-21-\d+-\d+-\d+-\d+", identity["sid"])
            and identity["sid"] == identity.get("local_sid"), "local_lab_user_required")
    checked(identity.get("administrator") is False and type(identity.get("session_id")) is int
            and identity["session_id"] > 0, "regular_interactive_user_required")
    venv = (root / "venv").resolve()
    checked(all(Path(package.get(key, "")).resolve().is_relative_to(venv)
                for key in ("package_path", "executable")), "owned_installed_interpreter_required")
    checked(bool(re.fullmatch(r"[0-9a-fA-F]{64}", marker.get("wheel_sha256", "")))
            and isinstance(package.get("wheel_sha256"), str)
            and package["wheel_sha256"].lower() == marker["wheel_sha256"].lower(),
            "installed_wheel_mismatch")
    checked(bool(re.fullmatch(r"[0-9A-Za-z.+-]{1,64}", marker.get("version", "")))
            and package.get("version") == marker["version"], "installed_version_mismatch")
    timestamp(identity["boot_utc"])
    timestamp(identity["observed_utc"])


def public_system(identity):
    return {key: identity[key] for key in
            ("boot_utc", "observed_utc", "uptime_ms", "caption", "version", "product_type", "session_id")} | {
                "user_sid_sha256": value_hash(identity["sid"])}


def preparation_diagnostic(result):
    """Retain bounded setup facts, never the full doctor receipt or credentials."""
    error = result.get("error", "")
    error = error if isinstance(error, str) else ""
    error = re.sub(r"[\x00-\x1f\x7f]", " ", error)
    error = re.sub(
        r'''(?i)\b(?:proxy-)?authorization["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}]+(?:\s+[^\s,;}]+)?)''',
        "Authorization: <redacted>", error)
    error = re.sub(r'''(?i)\bbearer\s+[^\s"',;}]+''', "Bearer <redacted>", error)
    # Paths may occur in filesystem exceptions; the path itself is not evidence.
    error = re.sub(r'''(?i)[a-z]:[\\/][^"']*''', "<path>", error)
    error = re.sub(r'''(?<![\w:/])(?:\\\\|/)[^\s"'<>]+''', "<path>", error)
    python = result.get("python")
    status = result.get("status")
    return {
        "python": python if isinstance(python, str) and re.fullmatch(r"[0-9A-Za-z.+-]{1,32}", python) else "unknown",
        "status": status if isinstance(status, str) and re.fullmatch(r"[a-z_]{1,32}", status) else "unknown",
        "ready": result.get("ready") if type(result.get("ready")) is bool else None,
        "error": error[:800],
    }


class Runtime:
    def guard(self, root, nonce):
        checked(os.name == "nt", "windows_only")
        checked(root.resolve() == FIXED_ROOT.resolve() and not root.is_symlink(), "fixed_guest_root_required")
        marker = load(root / "guest.json")
        identity, package = system_info(), package_info()
        validate_identity(marker, nonce, identity, package, root)
        return marker, public_system(identity), package["record_sha256"]

    def prepare(self):
        from genlayer_agent_lab.runtime import doctor
        result = doctor(timeout=900)
        try:
            checked(result.get("ready") is True
                    and result.get("provenance", {}).get("execution_success") is True,
                    "glsim_preparation_failed")
        except TrialError as exc:
            exc.preparation_diagnostic = preparation_diagnostic(result)
            raise

    def install(self, data):
        from genlayer_agent_lab import service
        return service.install(data, port=PORT, executable=sys.executable, start_now=True)

    def status(self, data):
        from genlayer_agent_lab import service
        return service.status(data)

    def task(self, manifest):
        from genlayer_agent_lab import service
        task = service._powershell(service._TASK_LOOKUP +
            "if($null -eq $task){@{exists=$false}|ConvertTo-Json -Compress}else{"
            "$x=[xml]$task.Xml;foreach($n in $x.SelectNodes(\"//*[local-name()='UserId']\")){"
            "if($n.InnerText -notmatch '^S-\\d(-\\d+)+$'){"
            "$a=New-Object Security.Principal.NTAccount($n.InnerText);"
            "$n.InnerText=$a.Translate([Security.Principal.SecurityIdentifier]).Value}};"
            "@{exists=$true;xml=$x.OuterXml;running=([int]$task.State -eq 4);enabled=$task.Enabled;"
            "last_run_utc=$task.LastRunTime.ToUniversalTime().ToString('o');"
            "last_result=$task.LastTaskResult}|ConvertTo-Json -Compress}", {"name": manifest["name"]})
        checked(task.get("exists") and service._task_matches(task.get("xml", ""), manifest),
                "managed_task_changed")
        # Match semantic identity using service._task_matches; Scheduler may add
        # harmless defaults to exported XML. Never export paths or raw XML.
        task["identity_sha256"] = value_hash({key: manifest[key] for key in
            ("name", "owner", "user", "executable", "launcher", "data_dir", "port", "platform")})
        return task

    def wait_ready(self, owner, deadline):
        import httpx
        with httpx.Client(trust_env=False, follow_redirects=False) as http:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    with http.stream("GET", f"http://127.0.0.1:{PORT}/service/health",
                                     timeout=min(1, remaining), headers={"Accept-Encoding": "identity"}) as response:
                        raw = bytearray()
                        for chunk in response.iter_raw():
                            raw.extend(chunk)
                            if len(raw) > 4096 or time.monotonic() >= deadline:
                                break
                        if (response.status_code == 200 and len(raw) <= 4096 and time.monotonic() < deadline
                                and json.loads(raw).get("service_id") == owner):
                            return
                except (httpx.HTTPError, ValueError, AttributeError):
                    pass
                time.sleep(min(.5, max(0, deadline - time.monotonic())))
        raise TrialError("automatic_readiness_timeout")

    def client(self, data):
        from genlayer_agent_lab.client import LabClient
        return LabClient(f"http://127.0.0.1:{PORT}", (data / "admin.token").read_text().strip(), timeout=5)


def run_agent(admin, deadline):
    checked(time.monotonic() < deadline, "run_deadline_exceeded")
    # Ambiguous POST failures are terminal; never submit the same trial twice.
    run_id = admin.create_run("escrow-normal", agent="safe", backend="glsim")["run_id"]
    checked(isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id), "invalid_run_identity")
    while time.monotonic() < deadline:
        if admin.get_run(run_id)["status"] in {"completed", "failed", "cancelled", "interrupted", "inconclusive"}:
            break
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
    checked(time.monotonic() < deadline, "run_deadline_exceeded")
    report = admin.report(run_id)
    checked(report.get("status") == "completed" and report.get("verdict") == "pass", "glsim_run_failed")
    checked(all(report.get("grades", {}).get(key, {}).get("status") == "pass"
                for key in ("decision", "behavior", "outcome", "completion")), "glsim_grade_failed")
    checked(report.get("manifest", {}).get("backend") == "glsim"
            and report["manifest"].get("runtime", {}).get("execution_success") is True,
            "actual_contract_evidence_missing")
    return run_id, value_hash(report)


def ready_state(state):
    checked(all(state.get(key) is True for key in ("installed", "enabled", "running", "ready")),
            "service_not_owned_enabled_and_ready")


def task_ready(task, system):
    checked(task.get("enabled") is True and task.get("running") is True
            and task.get("last_result") in {0, 0x41301}, "managed_task_not_running")
    checked(timestamp(task["last_run_utc"]) >= timestamp(system["boot_utc"]), "task_predates_boot")


def observe(root, nonce, runtime=None):
    """Run at a real logon. Dependency injection exists for mutation-free tests."""
    runtime = runtime or Runtime()
    marker, system, package_record = runtime.guard(root, nonce)  # Before any writes/service operations.
    if (root / "result.json").exists():
        return load(root / "result.json")
    result = {"schema_version": 1, "verification": "inconclusive", "checks": {},
              "scope": "Windows 11 guest OS reboot and regular-user automatic logon",
              "nonce": nonce, "wheel_sha256": marker["wheel_sha256"], "package_version": marker["version"],
              "studio_included": False, "before_login_startup": False,
              "manual_service_start_after_reboot": False}
    data = root / "data"
    baseline_path = root / "baseline.json"
    try:
        if not baseline_path.exists():
            claim(root / "baseline-attempt.json", system)
            checked(not data.exists(), "fresh_guest_data_required")
            result["stage"] = "baseline_runtime_preparation"
            runtime.prepare()  # Cold user cache; never executed after the reboot.
            result["stage"] = "baseline_service_installation"
            state = runtime.install(data)
            if state.get("ready") is not True:
                runtime.wait_ready(state["owner"], time.monotonic() + 120)
                state = runtime.status(data)
            ready_state(state)
            manifest = load(data / "service/installation.json")
            task = runtime.task(manifest)
            task_ready(task, system)
            result["stage"] = "baseline_actual_glsim_run"
            with runtime.client(data) as admin:
                run_id, report_hash = run_agent(admin, time.monotonic() + 180)
            baseline = {"schema_version": 1, "nonce": nonce, "system": system,
                        "version": marker["version"], "wheel_sha256": marker["wheel_sha256"],
                        "marker_sha256": digest(root / "guest.json"), "package_record_sha256": package_record,
                        "helper_sha256": digest(Path(__file__)), "owner": state["owner"],
                        "run_id": run_id, "report_sha256": report_hash,
                        "token_sha256": digest(data / "admin.token"),
                        "installation_sha256": digest(data / "service/installation.json"),
                        "task_identity_sha256": task["identity_sha256"],
                        "checks": {"guest_identity_verified": True, "regular_interactive_user": True,
                                   "actual_glsim_four_grades_pass": True, "owned_logon_service_ready": True}}
            save(baseline_path, baseline)
            return {"verification": "armed", "nonce": nonce, "checks": baseline["checks"]}

        baseline = load(baseline_path)
        if system["boot_utc"] == baseline["system"]["boot_utc"]:
            return {"verification": "pending_reboot", "nonce": nonce}
        claim(root / "after-attempt.json", system)
        result["stage"] = "post_reboot_identity"
        result.update(before=baseline["system"], after=system)
        checked(timestamp(system["boot_utc"]) > timestamp(baseline["system"]["observed_utc"]),
                "boot_did_not_follow_baseline")
        checked(system["user_sid_sha256"] == baseline["system"]["user_sid_sha256"], "user_changed")
        checked(all(system[key] == baseline["system"][key] for key in ("version", "caption", "product_type")),
                "operating_system_changed")
        result["checks"]["new_windows_boot_same_regular_user"] = True
        checked(baseline["nonce"] == nonce and marker["version"] == baseline["version"]
                and marker["wheel_sha256"] == baseline["wheel_sha256"]
                and digest(root / "guest.json") == baseline["marker_sha256"]
                and package_record == baseline["package_record_sha256"]
                and digest(Path(__file__)) == baseline["helper_sha256"], "trial_installation_changed")
        checked(digest(data / "service/installation.json") == baseline["installation_sha256"],
                "service_installation_changed")
        checked(digest(data / "admin.token") == baseline["token_sha256"], "credential_changed")
        manifest = load(data / "service/installation.json")
        result["stage"] = "post_reboot_automatic_startup"
        runtime.wait_ready(baseline["owner"], time.monotonic() + 120)
        state = runtime.status(data)  # Read-only. No after-phase install/start calls.
        ready_state(state)
        checked(state["owner"] == baseline["owner"], "service_owner_changed")
        task = runtime.task(manifest)
        task_ready(task, system)
        checked(task["identity_sha256"] == baseline["task_identity_sha256"], "managed_task_changed")
        result["checks"].update(automatic_ready_after_login=True, task_started_after_boot=True,
                                installation_and_credentials_preserved=True)
        result["task_last_run_utc"] = task["last_run_utc"]
        result["task_last_result"] = task["last_result"]
        result["stage"] = "post_reboot_actual_glsim_run"
        with runtime.client(data) as admin:
            checked(value_hash(admin.report(baseline["run_id"])) == baseline["report_sha256"],
                    "frozen_report_changed")
            run_id, report_hash = run_agent(admin, time.monotonic() + 180)
            checked(run_id != baseline["run_id"], "new_run_identity_reused")
            checked({baseline["run_id"], run_id} <= {row["run_id"] for row in admin.list_runs()},
                    "history_missing")
        result.update(verification="pass", baseline_run=baseline["run_id"], post_reboot_run=run_id,
                      baseline_report_sha256=baseline["report_sha256"], post_reboot_report_sha256=report_hash)
        result["checks"].update(frozen_report_and_history_preserved=True, new_actual_glsim_four_grades_pass=True)
        result["stage"] = "completed"
    except Exception as exc:
        result["error_code"] = str(exc) if isinstance(exc, TrialError) else "guest_verification_error"
        if (result.get("stage") == "baseline_runtime_preparation"
                and result["error_code"] == "glsim_preparation_failed"
                and isinstance(getattr(exc, "preparation_diagnostic", None), dict)):
            result["preparation_diagnostic"] = exc.preparation_diagnostic
        if result["error_code"] == "prior_attempt_incomplete":
            # An existing observer may still be running. Never overwrite its
            # evidence or re-submit work; a crashed claim remains terminal.
            return result
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    save(root / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guest-id", required=True)
    parser.add_argument("--root", type=Path, default=FIXED_ROOT)
    args = parser.parse_args(argv)
    try:
        result = observe(args.root, args.guest_id)
    except Exception as exc:
        result = {"verification": "inconclusive",
                  "error_code": str(exc) if isinstance(exc, TrialError) else "guest_guard_failed"}
    if sys.stdout is not None:
        print(json.dumps(result, indent=2))
    return 0 if result.get("verification") in {"pass", "armed", "pending_reboot"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
