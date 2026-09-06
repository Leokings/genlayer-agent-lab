# External developer installation trial

Use this checklist for each of the two independent human developer trials.
Automated CI and agent-assisted rehearsals are separate evidence. A developer
can use a coding agent to help, but should record where the instructions needed
clarification and whether they could integrate their own test agent.

## Prepare a fresh installation

Choose Windows, Linux or macOS on a machine you control. Record the OS version,
CPU architecture, Python version and whether Docker is installed. Python 3.12
is the verified setup; Docker is optional for the bundled GLSim cases. This
trial needs no payment card, model key, production wallet or shared cloud account.

Download the wheel and `SHA256SUMS` from the same
[published release](https://github.com/Leokings/genlayer-agent-lab/releases).
Record the exact release and verify the wheel's SHA-256 against that file.
Follow [INSTALL.md](INSTALL.md) to create a new environment and a new data
directory. Use the environment's absolute executable path for GUI/MCP clients.
Do not substitute a similarly named registry package: this alpha is supplied
through its release artifacts.

Export the included setup kit with `gl-agent-lab kit --output lab-kit`.
For prompt-assisted installation, give your coding agent the exported
`skills/setup-genlayer-agent-lab/SKILL.md` and the prompt in INSTALL.md. Record
each correction you had to make. Keep the same data directory for every step
below; supply `--data-dir` explicitly if it differs from the default.

## Complete these checks

1. Run `gl-agent-lab doctor`. Record the first preparation time and its result.
   The first run downloads about 217 MB; later runs reuse the verified cache.
2. Run `gl-agent-lab run escrow-normal --agent safe`. Expect a completed report
   with all four grades passing and exit code 0.
3. Run `gl-agent-lab run escrow-provisional --agent unsafe`. Expect an evaluation
   failure and exit code 1, including a failed behavior grade. A runtime error
   or inconclusive report is not the expected negative result.
4. Start `gl-agent-lab serve`. Open the dashboard at its reported loopback URL,
   authenticate locally, inspect both saved reports and export JSON or HTML.
   Keep the administrator token private; do not paste it in the trial report.
5. Choose Python, TypeScript or MCP from the exported `examples/README.md`.
   First run that independent client against `escrow-normal`. Then route the
   relevant tools of your own developer agent into the test interface. Record
   your framework, integration changes, run ID and result. Keep real wallet and
   production actions out of this test profile. Installing MCP alone does not
   redirect an agent's existing calls.
6. Stop and start the Lab process. Confirm the earlier report is unchanged and
   another bundled test succeeds. If testing optional automatic startup, follow
   [SERVICES.md](SERVICES.md) and record it separately from process restart.

If a step fails, retain its fixed error code and a redacted excerpt, then report
the blocker. Do not silently replace GLSim or Studio with `fixture` and count
that as real contract execution. Scripted reference agents check integration
and expected grading; their results are not evidence of a language model's skill.

## Optional checks

Custom contracts require the Linux Docker worker and
[CUSTOM_CONTRACTS.md](CUSTOM_CONTRACTS.md). Studio requires a larger Docker
resource budget and [STUDIO.md](STUDIO.md). Report these as separate extensions
to the base trial, with the selected binding/runtime and observed result.

Rebooting or logging out of the developer's computer is optional and requires
their agreement. Windows/macOS startup runs after login. A service restart does
not establish recovery from a whole OS reboot. The release verification record
states which platform checks have actually passed.

## Trial report template

```text
Trial ID and date:
Human developer alias (no email required):
Prior involvement in this project:
Release / wheel filename / wheel SHA-256:
OS version / architecture / Python version:
Docker present and version (if used):
Install method: manual / coding agent (name)
Fresh environment and data directory: yes / no
First doctor duration and result:
Safe run ID / four grades / exit code:
Unsafe provisional run ID / behavior grade / exit code:
Dashboard and export result:
Independent client: Python / TypeScript / MCP
Developer's agent/framework:
Tool integration changes required:
Developer-agent run ID / result:
Process restart: history preserved / new run result:
Optional startup, logout, reboot or Studio checks (exactly what happened):
Instruction corrections, blockers and time spent:
Redacted error codes or excerpts:
Outcome: completed / blocked / completed with documented workaround
```

Review reports before sharing: omit tokens, private keys, raw Studio receipts,
personal directories and private scenario evidence. A failed agent evaluation
can be a valid integration outcome; an infrastructure failure cannot be recorded
as a successful installation gate.
