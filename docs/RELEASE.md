# Alpha 6 release checklist

This is a developer alpha with controlled model responses. It is suitable for
reproducible agent integration experiments within the documented interfaces.
It does not certify an agent's general safety or performance with live models.

## What changed after alpha 5

- Corrected macOS ownership checks to preserve the exact spaces in LaunchAgent
  arguments while removing only launchctl's display indentation. Modified
  arguments and duplicate fields remain rejected.
- Wait for macOS job unloading to finish before reporting a successful stop;
  launchctl can return before its job disappears. The wait has a fixed deadline.
- Added an actual macOS LaunchAgent lifecycle gate in the runner's existing GUI
  session, plus a separate Ubuntu guest operating-system reboot gate with an
  outside observer. Consult the dated evidence before treating a gate as passed.

Consult [BUILD_STATUS.md](BUILD_STATUS.md) and [VERIFICATION.md](VERIFICATION.md)
for the actual evidence and remaining gates. Source tests, container checks,
remote OS jobs and human onboarding trials are separate results.

## Build the reviewable release bundle

From this checkout:

```sh
uv sync --locked --python 3.12
uv run --locked ruff check src tests scripts
uv run --locked pytest -q
uv build
uv run --locked python scripts/prepare-release.py
```

The final command requires a new destination, audits the wheel/source file lists,
then assembles the wheel, source archive, initial setup instructions, setup skill
and `SHA256SUMS` under `dist/release-0.1.0a6`. It never publishes anything. Preserve
the verified bundle unchanged and record its checksum when sharing it. The
source archive includes `uv.lock`; the wheel carries the installation kit.

Run [installed-artifact verification](INSTALL_VERIFICATION.md) against the exact
wheel being released. For recovery, `scripts/verify-upgrade.py` accepts
`--old-python`, `--new-python` and `--output` to rehearse historical report
preservation and backup rollback in temporary data directories. It runs fixture
cases and does not touch Studio or the normal installation.

## Publication and external gates

1. Use the project's own public repository,
   [Leokings/genlayer-agent-lab](https://github.com/Leokings/genlayer-agent-lab).
   The development checkout sits beneath an unrelated parent repository; never
   push that parent's files as the Lab project. Publication uses an isolated
   source checkout containing only the project's files.
2. Run the supplied CI workflow on Ubuntu, macOS and Windows. Inspect each
   installed-wheel artifact and the custom-contract Docker job. A workflow file
   alone does not close these gates. Confirm Actions usage fits the chosen
   account's free allowance or an explicitly accepted budget before using paid
   runners.
3. Have two developers install from the exact artifacts using only the supplied
   setup skill and documentation. Record OS/version, contract-execution evidence,
   safe/unsafe reports, HTTP or MCP integration and any undocumented intervention.
   Agent-assisted rehearsals are useful but do not count as these human trials.
4. Publish an explicitly marked prerelease with the checked artifacts, checksums,
   version pins, evidence and open limitations. Do not label it stable while these
   gates remain open. PyPI publication is optional and requires a chosen package
   owner and release authentication; local wheel installation already works.

The optional Studio checks retain their separate evidence for actual GenVM
deployment, approved/denied execution and appeals. The SQLite backup does not
restore Studio's persistent PostgreSQL volumes. Live inference, modern appeal
bonds and public-chain finality remain outside alpha 6.
