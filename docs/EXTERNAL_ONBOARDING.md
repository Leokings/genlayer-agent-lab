# First-agent onboarding trial

Use this checklist to find out whether an independent developer can install the current project Studio workflow, connect their own agent, and understand its first report. The primary instructions are [Install](INSTALL.md) and [Your first agent test](GETTING_STARTED.md); use [the VPS quickstart](VPS_QUICKSTART.md) when appropriate.

An automated demonstration or scripted reference run is separate evidence. A developer may use a coding agent and the supplied setup skill, but record every point that needed maintainer help. For the independent-developer gate, two environments owned by the same person do not count as two independent developers.

## What success looks like

The developer reaches a first completed run with the agent they actually want to test, without undocumented maintainer intervention. They can identify the connection method, see an actual agent request, and explain whether the report describes successful behavior, a failed expectation, or an infrastructure problem.

A failed agent evaluation can be a successful onboarding outcome if the connection worked and the report clearly explains the failure. A green scripted demo alone does not establish that the developer's own agent connected. An inconclusive runtime failure does not establish completed installation.

## Before the trial

Record the source revision or supplied artifact version, OS, CPU architecture, Python version, and Docker environment. The primary project path requires the supported Linux x86-64 Docker profile. Use a fresh environment and data directory for an installation trial. The repository and supplied artifacts are the distribution sources; do not substitute a similarly named registry package.

VPS access and operating-system dependencies remain external prerequisites. The bundled scripted verification does not require a paid model. The developer chooses the model and credentials for their own tested agent. Keep tokens, private keys, and private evidence out of the feedback record.

## Complete the journey

1. **Install and open the dashboard.** Follow the source or supplied-wheel instructions and run `gl-agent-lab setup`. Record time spent on prerequisites, downloads, and runtime preparation separately. Use `--no-open` plus an SSH tunnel for a VPS. Record any confusing or missing diagnostic.
2. **Choose and review a test.** Select a template, inspect its public task and supplied conditions, and read its expected behavior. Confirm that the developer can explain what should pass before creating the run. Custom-specification import is an advanced path, not a prerequisite for this first test.
3. **Connect the developer's own agent.** Use the generated MCP, HTTP, Python, or TypeScript instructions. Record the framework, model, configuration changes, and time to the first observed request. Note whether the developer mistook copying a snippet or pressing Check connection for the agent actually connecting.
4. **Complete and explain the run.** Have the agent observe its task, use the permitted tools, and finish. Record the run ID and result. Ask the developer to point to the action or check that explains the outcome, using the readable report before opening Advanced JSON.
5. **Run one variation.** Change a supported template option or choose another relevant template. Review the new expectations and run the same agent again. Record whether the developer understood what changed and could compare the outcomes.
6. **Return to the installation.** After runs have finished, stop and start the Lab process. Confirm that the earlier report remains available and the developer knows how to create a new test. Optional startup, logout, or whole-machine reboot checks should be recorded separately.

If a prerequisite or connection blocks progress, retain the exact safe error code and report what the developer tried. Do not silently switch to a different runtime and call the original path successful. For diagnosis, a separate `project verify --case prediction` run can distinguish a scripted installation check from the developer-agent trial.

The setup skill can be read locally from `skills/setup-genlayer-agent-lab/SKILL.md` in the checkout or exported kit, or from [its source URL](https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md). If installed in the coding agent, a later session should need only a short request to start the existing Lab. Record whether that worked without repeating the entire setup procedure.

## Record these measures

| Measure | What to record |
|---|---|
| Time to dashboard | Elapsed time, separating downloads from active effort |
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
