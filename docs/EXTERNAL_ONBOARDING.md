# First-agent onboarding trial

Use this checklist to find out whether an independent developer can install the current project Studio workflow, connect their own agent, and understand its first report. The primary instructions are [Install](INSTALL.md) and [Your first agent test](GETTING_STARTED.md); use [the VPS quickstart](VPS_QUICKSTART.md) when appropriate.

An automated demonstration or scripted reference run is separate evidence. A developer may use a coding agent and the supplied setup skill, but record every point that needed maintainer help. For the independent-developer gate, two environments owned by the same person do not count as two independent developers.

## What success looks like

The developer starts with an existing terminal-capable agent and pastes the installation prompt once. The agent prepares missing software, opens a usable Lab, and resumes across necessary local user actions without undocumented maintainer intervention. The developer then completes a reviewed run with the agent they want to test and can explain its actual connection and result.

A failed agent evaluation can be a successful onboarding outcome if the connection worked and the report clearly explains the failure. A green scripted demo alone does not establish that the developer's own agent connected. An inconclusive runtime failure does not establish completed installation.

## Before the trial

Record the source revision or supplied artifact version, OS, CPU architecture, Python version, and Docker environment. The primary project path requires the supported Linux x86-64 Docker profile. Use a fresh environment and data directory for an installation trial. The repository and supplied artifacts are the distribution sources; do not substitute a similarly named registry package.

VPS ownership/access and an existing terminal-capable agent are the starting conditions. Installing missing OS dependencies is part of the agent-led journey. Record any password, OS dialog, login or reboot handoff separately from maintainer help. The developer keeps their chosen agent/model. Keep tokens, passwords, private keys and private evidence out of the feedback record.

## Required acceptance paths

These are trial requirements, not results already established by the documentation or a mocked helper test.

| Starting state | What the trial must establish |
|---|---|
| Fresh supported host without Docker | Paste the installation prompt; agent detects the host and installs missing dependencies, then verifies Docker service access, Compose, Studio readiness, Lab health and dashboard access. A package-install exit code alone is insufficient. |
| Partially installed host | Agent reuses existing Git/uv/Docker, checkout, selected Docker context, data and completed build stages; installs only missing pieces and preserves unrelated software/model settings. |
| Password or Docker-group login handoff | Agent states the exact local action, saves the absolute helper path and progress without secrets, and resumes after the user completes it. Its actual running process has refreshed groups; no password enters chat and no restart begins installation again. |
| Desktop dialog or reboot required | Agent saves the stage, leaves reboot/dialog control to the user, and resumes with the same agent after return; record the actual platform rather than extending one platform's result to another. |
| Unsupported architecture or unavailable tools | Agent names the missing capability and concrete next action, without claiming setup completed or silently substituting an unsupported runtime. |

## Complete the journey

1. **Install and open the dashboard.** Paste the [installation prompt](INSTALL.md#setup-with-an-agent) into the existing agent. Record dependency installation, downloads, runtime preparation and required user actions separately. On a VPS, verify tmux detach/return and the SSH tunnel. For each prerequisite, record whether it was already present or installed during this trial.
2. **Choose and review a test.** Select a template, inspect its public task and supplied conditions, and read its expected behavior. Confirm that the developer can explain what should pass before creating the run. Custom-specification import is an advanced path, not a prerequisite for this first test.
3. **Connect the developer's own agent.** Paste the dashboard's generated **agent setup prompt** into a tested-agent context without private grading. Record framework, model, configuration changes, any runtime-restart handoff, and the first observed request. Confirm the developer can distinguish this run-specific message from the earlier installation prompt.
4. **Complete and explain the run.** Have the agent observe its task, use the permitted tools, and finish. Record the run ID and result. Ask the developer to point to the action or check that explains the outcome, using the readable report before opening Advanced JSON.
5. **Run one variation.** Change a supported template option or choose another relevant template. Review the new expectations and run the same agent again. Record whether the developer understood what changed and could compare the outcomes.
6. **Return to the installation.** After runs have finished, stop and start the Lab process. Confirm that the earlier report remains available and the developer knows how to create a new test. Optional startup, logout, or whole-machine reboot checks should be recorded separately.

If a prerequisite or connection blocks progress, retain the exact safe error code and report what the developer tried. Do not silently switch to a different runtime and call the original path successful. For diagnosis, a separate `project verify --case prediction` run can distinguish a scripted installation check from the developer-agent trial.

The setup skill can be read locally from `skills/setup-genlayer-agent-lab/SKILL.md` in the checkout or exported kit, or from [its source URL](https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md). If installed in the coding agent, a later session should need only a short request to start the existing Lab. Record whether that worked without repeating the entire setup procedure.

## Record these measures

| Measure | What to record |
|---|---|
| Time to dashboard | Elapsed time, separating downloads from active effort |
| Dependency setup | Initially missing tools, what the agent installed, actual daemon/readiness checks |
| Resume quality | Exact user handoffs, progress retained, duplicate work or lost settings |
| Time to first real-agent request | From opening connection instructions to an observed request by the developer's agent |
| Unassisted completion | Whether the supplied instructions and setup skill were sufficient without maintainer intervention |
| Connection clarity | Whether the developer could distinguish configured, waiting, and observed use |
| Report clarity | Whether the developer could explain the result and identify the next action |
| Repeat use | Whether the developer could return later and create a second run without repeating installation |

## Trial report

```text
Trial ID and date:
Developer alias / prior involvement in the project:
Source revision or artifact filename / checksum:
OS / architecture / Python / Docker environment:
Fresh installation and data directory:
Manual setup or coding agent / installed setup skill:
Starting dependencies present / missing, including Docker:
Single installation prompt / extra prompts needed:
Password, OS dialog, login or reboot handoffs / same-agent resume:
Docker service + Compose / Studio readiness / Lab health evidence:
Prerequisite, download, and active setup time:
Time to dashboard / blockers:
First template / expectation understood before creation:
Developer's own agent framework / model:
Connection method / integration changes:
Time to first observed request / connection indicator understood:
First real-agent run ID / result / developer's explanation:
Second run ID / changed condition / comparison understood:
Optional scripted check ID / result, recorded separately:
Return session / history retained / new run possible:
Maintainer help or instruction corrections required:
Optional startup, logout, or reboot checks actually performed:
Redacted diagnostic codes / report locations:
Outcome: completed unassisted / completed with help / blocked
```

Share only the report material needed to explain a blocker. A service restart does not establish whole-machine reboot recovery; use the dated [verification record](VERIFICATION.md) for the exact platform evidence.
