# Start the lab automatically at user login

The service integration runs the lab in your user session with an explicit data directory and a loopback address. Windows uses a Scheduled Task, Linux uses `systemd --user`, and macOS uses a LaunchAgent. It installs no system daemon and requests no administrator privileges.

Install the package into a persistent virtual environment first. Keep that environment and its project/package installation in place: startup records an absolute Python executable path. Moving or deleting the environment requires uninstalling the startup definition and reinstalling it with the new interpreter. A Unix virtual environment's Python symlink is preserved as a lexical path, so startup retains that environment's packages. Temporary `uvx`/build environments are rejected; a persistent `uv tool` or pipx installation must also remain installed at the recorded path.

```text
gl-agent-lab --data-dir /absolute/path/to/lab service install --port 8765
gl-agent-lab --data-dir /absolute/path/to/lab service start
gl-agent-lab --data-dir /absolute/path/to/lab service status
gl-agent-lab --data-dir /absolute/path/to/lab service stop
gl-agent-lab --data-dir /absolute/path/to/lab service uninstall
```

Use `service install --start` to register startup and start immediately. Without `--start`, registration and starting are separate on Windows and Linux; macOS may start the LaunchAgent when it is loaded because `RunAtLoad` is enabled. All commands use the same data directory. Existing foreground servers should be stopped through their own terminal before starting the service; a port conflict never causes this integration to kill an unrelated process.

`install` and `start` return exit status 2 if installation/readiness was not established. `stop` returns 2 if stopping was not established. `status` returns 0 for an installed service even when stopped; inspect `running` and `ready` to distinguish those states. Infrastructure, permission and ownership errors return 2. The readiness check reaches the wrapper's `/service/health` endpoint on `127.0.0.1` and verifies the installation identity rather than accepting an unrelated server on the same port.

## What is installed

The service name includes a deterministic identity derived from the canonical data directory, current user and operating system. Separate installations receive separate identities. Definitions contain the fixed module invocation and paths, never an administrator token, wallet key or model credential.

| Platform | Definition and behavior |
| --- | --- |
| Windows | A current-user Scheduled Task in the root task folder, with an interactive logon trigger and least privileges. A sibling `pythonw.exe` is used when available to avoid a console window. No user password is stored. |
| Linux | `~/.config/systemd/user/genlayer-agent-lab-<id>.service`, enabled for the user manager's `default.target`. A working user manager/login session is required. The installer does not enable lingering or create a boot-time system service. |
| macOS | `~/Library/LaunchAgents/genlayer-agent-lab-<id>.plist`, loaded into `gui/<uid>`. A GUI login session is required. Stopping unloads the current job; the definition remains for the next login or an explicit start. |

Windows account names exported by Task Scheduler are resolved back to SIDs before ownership comparison. Scheduler XML defaults are normalized; a changed executable, arguments, account or privilege policy is rejected. Linux checks the actual loaded fragment, pending reload state and drop-in paths. macOS checks the loaded job's source, executable and argument list. Foreign or edited definitions are never overwritten or stopped. To intentionally customize a definition, first uninstall this managed startup integration and manage the replacement yourself.

The wrapper binds only `127.0.0.1`; supported service ports are 1024–65535. It clears ambient `LAB_URL`, `LAB_TOKEN` and `LAB_DATA_DIR` so they cannot redirect this explicitly configured installation. It preserves the normal user-session environment needed by Python and local tools. Service setup does not start Docker Desktop or the optional Studio stack: those dependencies must be available separately for tests that require them.

## Logs, stops and upgrades

Logs are stored in `<data-dir>/service/service.log`, rotating at 2 MiB with two backups. HTTP access logging is disabled. Metadata and the Windows XML copy live beside the log. Stop/uninstall preserve the administrator token, database, reports, imported contracts, Studio state and logs. Uninstall removes only the managed startup registration/definition and its installation manifest.

Linux and macOS send the process a normal termination request. Windows Task Scheduler can terminate its running task directly; an in-progress run may be interrupted. The lab recovers interrupted runs from its persistent state on restart. Finish important test runs before stopping the service.

To change the interpreter or port, uninstall the startup definition, then install it again with the same data directory and desired settings. To update package files in place, stop the service before the update and start it afterward. If a release changes the generated startup definition, reinstall that definition; the ownership check deliberately refuses to overwrite an unexpected existing version.

## Upgrading an alpha 4 Linux registration

Alpha 4 generated a quoted `WorkingDirectory` value that systemd rejected as
`bad-setting`. Alpha 5 starts in the user's home and enters the exact data
directory in Python after validating the startup identity. If an alpha 4 Linux
installation left a startup registration behind, run `service uninstall` with
the retained alpha 4 executable **before** switching versions, then install the
startup service with alpha 5 against the same data directory. Uninstall preserves
the database, token and logs. A newer executable intentionally refuses to change
a definition that differs from its own expected format.

## Verification and platform limits

This release has focused tests for deterministic ownership, shell-independent quoting, exported Windows defaults, definition tampering, Linux overrides, LaunchAgent argument verification, persistent interpreter paths and data preservation. The Windows lifecycle was exercised on the development host using a separate `.lab/service-test` installation and port 8875. Alpha 5 also passed an actual systemd user-service lifecycle on an Ubuntu 24.04 CI host: fresh wheel installation, two bundled GLSim runs over HTTP, stop/start with preserved reports, and uninstall with preserved data. The host prepared a linger-enabled user manager for a disposable account; the package installer does not enable lingering or elevate itself. Native macOS startup, full-machine reboot/logout and automatic Studio startup remain unverified. See [VERIFICATION.md](VERIFICATION.md) for the dated CI evidence.

Some organizations prohibit current-user Scheduled Task registration. If Windows returns access denied, the command reports that restriction and does not escalate privileges. Linux without a user session manager and macOS without a GUI login session similarly report an unsupported current session; use the foreground `serve` command in those environments.

Implementation references: Microsoft's [Task Scheduler schema](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-schema) and [registration API](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertask); systemd's [service specification](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml) and [unit syntax](https://github.com/systemd/systemd/blob/main/man/systemd.syntax.xml); Apple's [LaunchAgent configuration](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) and [launchctl guidance](https://support.apple.com/guide/terminal/script-management-with-launchd-apdc6c1077b-5d5d-4d35-9c19-60f2397b2369/mac).

## Python integration

`genlayer_agent_lab.service` exports `install(data_dir, *, port=8765, executable=None, start_now=False)`, `status(data_dir)`, `start(data_dir)`, `stop(data_dir)` and `uninstall(data_dir)`. They return JSON-safe dictionaries and raise `ServiceError` with a safe `code` and message on failure. Installation/status dictionaries include `installed`, `running`, `ready`, `enabled`, the definition path, interpreter path and local endpoint. No returned value contains an authentication token.
