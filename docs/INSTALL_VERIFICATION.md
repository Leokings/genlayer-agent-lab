# Verifying a distributable installation

The release gate installs the built wheel into a new virtual environment and runs from a new working directory outside the checkout. It does not treat tests against an editable source installation as evidence that a wheel works.

For the two human developer installation trials, use the separate
[external onboarding checklist and report template](EXTERNAL_ONBOARDING.md).
Its completed reports complement CI; exporting the checklist is not a passed
external trial.

```sh
uv build --out-dir dist-ci
uv run python -I scripts/verify-install.py \
  --wheel 'dist-ci/genlayer_agent_lab-*-py3-none-any.whl' \
  --require-kit \
  --output .lab/install-verification.json
```

The quoted wheel glob must select exactly one artifact. Use a separate output directory for each build rather than accidentally testing an older wheel. `--require-kit` requires the packaged integration kit introduced in alpha 4. Omit it only when checking an earlier artifact. `--backend glsim` additionally runs all 18 scenarios through the trusted bundled contract; the default uses 18 fixture cases plus the doctor's actual GLSim execution.

The script performs these checks:

1. Creates a fresh virtual environment, home directory, cache and application state under an owned system temporary directory. Installs the wheel and its declared dependencies from the public Python package index with pip's cache disabled.
2. Copies only the verification script into that temporary directory and runs it with the installed interpreter's `-I` isolation. Confirms the imported package comes from the fresh environment and that distribution metadata matches its version.
3. Reads and hashes required packaged assets, the bundled intelligent contract, the Dockerfile, dependency lock and Studio relay. Checks the installed console entry point, initializes fresh state, and runs `doctor`. The doctor must execute the actual pinned trusted contract successfully.
4. Requires all 18 scenario cases to pass all four grades. With `--require-kit`, exports the packaged skill, Python/MCP/TypeScript clients and installation/service/recovery documentation into a new directory and requires those files to be nonempty.
5. Starts a separate installed HTTP service on an ephemeral loopback port. Requires authentication, verifies dashboard resources, completes a run through the installed Python HTTP client, and checks the server's persisted grades.
6. Completes a second run through an actual MCP stdio subprocess and verifies the five run-scoped tools. Neither the agent's own claimed success nor a successful tool call replaces the server-side grades.

Child processes receive an explicit environment allowlist. Host model/wallet/cloud credentials, `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV`, Lab connection settings, proxy variables and package-index credentials are not inherited. HTTP/MCP tokens are freshly generated for the temporary installation and never included in the JSON report. The verifier stops only its own service and removes only its own temporary directory.

Initial installation needs internet access to download declared Python dependencies and the pinned GenVM bundle. This exercises a clean cache; it does not call a paid model provider or a public chain. The fixture suite and trusted GLSim doctor do not require Docker or Studio.

The pinned universal GenVM archive is about 217 MB. The first `doctor` preparation therefore has a 900-second budget, with a separate 960-second subprocess guard in this verifier. The complete installed-package probe is bounded to 1,800 seconds. Ordinary decision execution retains its shorter timeout. A failed or timed-out doctor fails the installation check; the verifier never substitutes a fixture result for runtime readiness.

## CI and local evidence

`.github/workflows/ci.yml` defines Ubuntu, macOS and Windows jobs that build the artifact, perform this clean installation check, and upload its sanitized JSON report. The separate Ubuntu custom-contract Docker gate is retained. A workflow definition is an authored gate; a green GitHub Actions run and its artifact are the evidence that the corresponding runner actually passed it.

A local Linux Docker check can install the same wheel inside a fresh official Python Linux image. Mount only the wheel read-only, pass the verification script over stdin, and provide no host virtual environment, caches, credentials, project source or Docker socket. A temporary filesystem containing the fresh virtual environment needs explicit `exec` permission so its installed console scripts and Python extensions can run; the container root can remain read-only. Retrieve the sanitized report from that owned container and remove only that container afterward. This establishes Linux userspace behavior inside Docker on the current host; it is not evidence of a native Linux or macOS runner.

The JSON report records the wheel SHA-256, Python/platform identity, installed distribution versions, resource hashes, doctor evidence, suite counts and HTTP/MCP results. It distinguishes the fixture scenario suite from actual trusted GLSim execution. Studio consensus, appeals, custom contracts and remote operating-system execution retain their separate verification records. After successful Ubuntu installation verification, CI also retains the exact wheel and source archive in `tested-linux-distributions` for three days. Release preparation uses these artifacts and checks the wheel hash against the installation report.

## Linux service verification without a VPS

The public repository can use a standard GitHub-hosted Ubuntu virtual machine
to exercise the real systemd user service. This is a short-lived automated
test, not a hosted Lab installation. It needs no DigitalOcean account or
payment card. Standard runner execution is free for public repositories;
larger runners and storage have separate billing rules in the
[GitHub Actions documentation](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

The dedicated Linux service check installs a wheel into a persistent virtual
environment belonging to an isolated CI user, outside the source checkout.
It checks service registration, real bundled GLSim execution through HTTP,
stop/start with preserved reports, and removal of the startup registration.
Its compact evidence is printed in the workflow log rather than uploading
application data or credentials.

The CI host prepares a systemd user manager for the test account. This host
preparation is separate from the package's current-user installer, which does
not grant itself privileges or enable lingering. A passing test establishes
the service lifecycle on that runner. It does not establish recovery from a
whole-machine reboot, a macOS GUI login, Studio-volume disaster recovery, or
independent human onboarding.

## Native macOS service verification

`.github/workflows/macos-service.yml` runs `scripts/verify-macos-service.py` on
a standard macOS 15 ARM64 runner. It first requires the runner's real GUI login
session. The script installs the supplied wheel into a fresh persistent virtual
environment under a uniquely owned home directory child, then exercises the
actual LaunchAgent through install, start, stop, restart and uninstall.
Two real bundled GLSim HTTP runs must pass, and saved reports and credentials
must survive restart. The verifier checks that uninstall removes the loaded job
and definition while retaining data, then removes only its own installation.
Its JSON evidence includes no tokens or raw launchctl environment output.

## Process restart, login and full-machine reboot

These are different checks with different evidence:

| Check | What changes | Required observation |
| --- | --- | --- |
| Service restart | Only the Lab process stops and starts; the operating system stays running | The startup manager can control the Lab, its saved report is unchanged, and a new test succeeds |
| Logout and login | The user's desktop session ends and a new one begins | The Lab starts from the real login trigger without a manual start command |
| Full operating-system reboot | The operating system and all processes restart on the same persistent disk | A new boot identity, automatic Lab readiness under the configured startup policy, preserved history, and successful new execution |

The macOS service workflow checks a real LaunchAgent in the runner's existing
GUI session. A bootstrap/bootout cycle does not log the user out or back in.
Similarly, manually starting a Scheduled Task or systemd unit does not establish
automatic recovery after reboot.

A reboot test needs an observer outside the machine being restarted. Running a
second ordinary GitHub job supplies a different virtual machine and therefore
cannot establish reboot persistence. A persistent guest virtual machine can be
rebooted while its outer test runner stays alive; evidence must explicitly name
the guest operating system and distinguish that from restarting the provider's
host. The Lab's user-session startup policy and any separately configured Linux
lingering policy must be recorded. The optional Docker/Studio stack has its own
startup and persistent-volume requirements.

`.github/workflows/linux-reboot.yml` exercises that guest approach with a pinned,
signature- and checksum-verified Ubuntu 24.04 cloud image under KVM. The CI host
grants its regular runner user access through the KVM group. Inside the guest,
an administrator enables lingering for the separate Lab account. The verifier
records a completed bundled GLSim run, reboots only that owned guest, then uses
a different observer account to verify a new boot identity and automatic HTTP
readiness before any Lab login or manual service start. It requires unchanged
credentials, installation identity and frozen report, then a successful new
GLSim run. The outer runner's boot identity must remain unchanged. Cleanup removes
the disposable guest disk and keys; compact evidence is retained in CI logs.

`.github/workflows/windows-reboot.yml` implements a separate, manually dispatched
Windows guest trial. Its `preflight` mode measures actual free disk, checks the
firmware and creates an empty KVM VM as the regular hosted Linux user. Its `trial`
mode downloads the exact published alpha 7 wheel and Microsoft's Windows 11
Enterprise 25H2 evaluation ISO, verifies their pinned SHA-256 digests, and installs
Windows on an empty private 80 GiB virtual disk. It requires at least 35 GiB of
actual free space. Firmware has Microsoft Secure Boot keys, and the guest has a
TPM 2.0 device, two virtual CPUs and 8 GiB of RAM.

The test uses Microsoft's documented unattended installation, local-account and
AutoLogon settings. AutoLogon is configured only inside the disposable VM; the
Lab installer does not change a developer's login policy. The guarded specialize
step copies the private seed and arms Windows' documented `SetupComplete.cmd` hook.
That later SYSTEM hook installs Python and the published wheel, starts the
independent observer, and returns without waiting for user login or requesting a
reboot. A separate non-administrator `LabUser`
prepares GLSim, installs the Lab's own user service and completes the first real
HTTP run. Existing setup code is read-only to that user; it can create and manage
its own test data and evidence.

Before requesting a reboot, the Linux observer retains the guest's boot time,
service identity, credential/report digests and completed-run evidence. The guest
accepts one reboot command only when its VM serial, random trial marker and
baseline-file digest match. After the next real Windows boot and user login, the
probe only observes automatic service readiness; it never calls service install,
start, restart or doctor in that phase. It requires unchanged credentials and
history plus a fresh GLSim execution passing all four grades. The same QEMU
process, guest disk and outer Linux boot identity must survive.

Compact JSON is retained. While awaiting the first baseline, the trial can emit
at most four setup/login screens to CI logs, ten minutes apart, with a 96 KiB PNG
limit per screen. It never takes those periodic images during reboot recovery.
On failure, the guest's last screen is also retained as a one-day artifact.
The trial deletes its private guest disk, installation media, unattended passwords
and logs. This tests orderly **Windows guest OS reboot followed by login**. It does
not establish recovery before login, physical-machine power loss, macOS behavior,
or automatic Docker/Studio startup. The actual outcome belongs in
[VERIFICATION.md](VERIFICATION.md); creating the workflow does not close that gate.

The Windows evaluation image is for bounded evaluation under Microsoft's terms,
and is downloaded from Microsoft's own distribution rather than republished by
this project. Sources: [evaluation media](https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise),
[setup scripts](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/add-a-custom-script-to-windows-setup?view=windows-11),
[AutoLogon](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-shell-setup-autologon),
[local accounts](https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-shell-setup-useraccounts-localaccounts-localaccount).
