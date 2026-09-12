# GenLayer Agent Lab

Test how your agent uses GenLayer decisions before relying on it in an application. Choose Studio execution or a quick GLSim simulation in one dashboard, review a supported scenario, connect your agent, and inspect its decisions and actions.

[Open setup page](https://genlayer-agent-lab-setup.vercel.app) · [Get started](docs/GETTING_STARTED.md) · [Install](docs/INSTALL.md) · [Use a VPS](docs/VPS_QUICKSTART.md) · [Source](https://github.com/Leokings/genlayer-agent-lab)

**Version 0.1.0a12 — development candidate.** See [build status](docs/BUILD_STATUS.md), the dated [verification record](docs/VERIFICATION.md), and the [build audit](docs/BUILD_AUDIT.md) for completed and open gates.

## Start here

Open the [public setup page](https://genlayer-agent-lab-setup.vercel.app) and copy its prompt into an agent that already has terminal access to your computer or VPS. The page needs no Lab installation, account or login. It is a public instruction page; your Lab, Studio and test data run on your own machine.

You do not need to install Git, Python, uv or Docker yourself: the prompt asks your agent to install what is missing and reuse what is ready. You can also copy it directly here:

```text
Set up GenLayer Agent Lab on this machine using https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md. I authorize installing missing prerequisites, including Git, uv/Python and Docker with Compose, then installing and starting the Lab and its owned Studio environment. First detect this host, architecture, available access and existing installation. Briefly state the changes, then proceed within your available permissions. Preserve my agent, model, unrelated settings, software and saved data. Reuse completed setup stages. Pause only when a password, OS dialog, login, reboot or unavailable capability requires me; never request passwords in chat or reboot automatically. Before an interruption, save a secret-free progress note and give the exact next action so we can resume with this same agent. On a VPS, leave long setup stages in tmux and tell me how to return. Verify the running Docker service, Studio readiness and Lab health. Guide me through opening the dashboard one human step at a time. Use my known connection app, or ask whether I use Termius or Windows PowerShell. Give exact Termius field values or one ready-to-paste PowerShell command using my known server, username and actual ports; ask only for missing connection details, never fetch passwords or private keys. Use the setup page's Open my dashboard flow as a guide, not a link-only handoff. Do not dump generic SSH placeholders or ask me to retrieve administrator keys. Use the dashboard's Connect flow: I will paste its short approval request into this setup conversation for you to approve with gl-agent-lab dashboard approve CODE against this installation. Then help me review a first test. Keep workspace keys and private grading out of the tested agent's context; use the dashboard's generated agent setup prompt only after I review and create that test.
```

Passwords and operating-system prompts stay on your device. If setup needs a login or reboot, complete the stated action and tell the same agent to resume. The [installation guide](docs/INSTALL.md) describes supported hosts. A downloaded checkout or installation kit also includes [START.html](docs/START.html) for local use; GitHub displays that file as source.

Already installed? Use **Open my dashboard** on the setup page. It guides VPS users through their existing Termius connection. Choose **Connect this browser** in the dashboard and paste its sign-in request into your setup agent; the browser opens after approval. No administrator key needs copying. Your setup agent gives you one required action at a time.

Have your own contract? Choose [Prepare a test from my contract](https://genlayer-agent-lab-setup.vercel.app/#prepare-contract-test), or **Use my own contract** under **Studio execution**. Copy its prompt into your authoring agent and provide your code and test idea. The [test-preparation skill](skills/prepare-genlayer-lab-test/SKILL.md) produces a supported project binding and self-contained project-v2 JSON draft for Studio. That file is not a GLSim scenario. You review it before starting; use a separate test context that cannot access the authoring conversation, memories or private grading files.

After you review and create a test, the dashboard provides **Copy agent setup prompt**. Paste that message into the agent you want to test: it includes the mode's connection settings and asks the agent to configure its tools and begin. Every new run needs its own prompt and scoped credential, including when changing modes. This works with agents that can manage their tool configuration; a chat-only client may still need its owner's connection settings changed. Manual connection instructions remain available.

Studio project runs allow up to 60 minutes for preparation and connection. Their selected test time starts once Studio is ready and the agent has actually called `observe`. Bundled quick simulations instead have ten minutes after runtime preparation; prepare the agent host before creating one, then connect promptly.

For an advanced terminal installation, the commands below assume Git, [uv](https://docs.astral.sh/uv/getting-started/installation/) and Docker with Compose are ready. The agent setup path above includes those prerequisites. Setup uses Python 3.12 and Linux x86-64 containers.

```sh
git clone https://github.com/Leokings/genlayer-agent-lab.git
cd genlayer-agent-lab
uv sync --locked --python 3.12
uv run gl-agent-lab setup
```

`setup` checks prerequisites, prepares or starts this installation's owned project Studio stack, starts the Lab, and opens the workflow dashboard. The first build downloads pinned dependencies and can take tens of minutes. Keep the setup terminal open while its foreground Lab is running. Use `setup --check` when you want a read-only readiness check.

Already installed? Return to the same directory and run `uv run gl-agent-lab setup` again. On a server, use `setup --no-open` and the [SSH tunnel instructions](docs/VPS_QUICKSTART.md).

## Run your first test

1. **Choose a mode, then a test.** **Studio execution** is the default; select a template or import a custom project file. **Quick simulation · GLSim** offers its own supported scenarios.
2. **Review the test.** Read its task, supplied conditions, permissions, and expected behavior.
3. **Create and connect.** Use **Create test & connect agent** for Studio or **Create quick test** for GLSim. Select where your agent runs, then paste **Copy agent setup prompt** into that agent. The message contains this run's supported connection and start instructions.
4. **Confirm agent activity.** Look for actual agent requests, then inspect its actions and the readable report. The unified history identifies each run's actual backend and retains earlier Studio and fixture reports.

The [first-run walkthrough](docs/GETTING_STARTED.md) explains each step. A scripted example can check your installation without a paid model; testing your own agent uses that agent's model and tool integration. No OpenClaw installation is required.

## What you can test

| Dashboard mode | Scope |
|---|---|
| **Studio execution** (default) | Supported project contracts, actual local execution, observed finality and permitted protocol appeals |
| **Quick simulation · GLSim** | Supported contract evaluation with controlled inputs and a scripted consumer timeline; application effects are simulated |

Quick mode offers normal, provisional, timeout, wrong-scope and duplicate-acknowledgement cases for escrow, treasury and generic consumers. Scripted statuses are not observed GenLayer finality. Quick mode has no appeal scenarios and does not simulate protocol appeals. Modes have different scenario formats and tool interfaces; project-v2 JSON remains Studio-only.

[Project workflows](docs/PROJECT_WORKFLOWS.md) support declared typed operations and results, multi-file contracts, defined effects across contracts, actual local appeals, observed fee accounting, and recovery after a Lab interruption with Studio intact. The developer supplies possible contract model responses and separately reviewed grading rules.

[Investigation scenarios](docs/INVESTIGATION.md) test missing, stale, conflicting, or misleading evidence. They record the agent's findings, its proposed response, the decision it investigated, and the actions it actually took. [Scenario authoring](docs/SCENARIO_AUTHORING.md) explains how to adapt a template or use your own authoring agent.

The dashboard keeps mode selection, supported test choices, agent connection and history together. The former developer-dashboard URL redirects here. The Lab tests agent behavior in the selected supported environment; contract-model judgment quality and public-network settlement are outside scope. Your agent needs to use that run's Lab interface for the actions under test.

## How this relates to GenLayer Studio

Studio already provides a GenLayer development and testing environment. The Lab builds on that environment: it supplies reviewed agent scenarios, connects the agent's test tools, records its actions, and checks those actions against your expected behavior. For example, a run can check whether an agent waits for a final decision, handles an appeal, and records the resulting outcome exactly once.

Developers can build their own agent test harness around Studio. The Lab packages that work into reusable tests and readable reports. Its guided flow reduces setup and interpretation work; connecting an unsupported agent or custom project can still require development work.

## Setup skill

Give it the [setup skill](skills/setup-genlayer-agent-lab/SKILL.md), also available at [its source URL](https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md):

> Use setup-genlayer-agent-lab to reopen my installation and help me connect my agent to a reviewed test.

If your agent supports installed local skills, add that skill through its normal skill mechanism. Later, use a short prompt such as “Start my existing GenLayer Agent Lab and help me create a new agent test.” The skill also ships in the exported installation kit.

## Next steps

- [Install or use a supplied wheel](docs/INSTALL.md), [manage startup](docs/SERVICES.md), or [back up an installation](docs/RECOVERY.md).
- [Connect or adapt your agent](docs/PROJECT_WORKFLOWS.md#connecting-an-agent) and inspect [runnable examples](examples/README.md).
- [Describe your project](docs/PROJECT_BINDINGS.md), [author scenarios](docs/SCENARIO_AUTHORING.md), or [test evidence use](docs/INVESTIGATION.md).
- [Report an onboarding trial](docs/EXTERNAL_ONBOARDING.md) or [troubleshoot an error](docs/TROUBLESHOOTING.md).

Legacy binding import, `service_release`, and earlier Studio commands remain advanced compatibility paths documented in [the examples](examples/README.md), [legacy Studio workflows](docs/STUDIO_WORKFLOWS.md), and [custom contracts](docs/CUSTOM_CONTRACTS.md). Quick mode can select already imported compatible legacy bindings for isolated `container-glsim` execution; it does not convert Studio project imports. Earlier reports retain their original backend and evidence limits.

## Development

```sh
uv run pytest -q
uv run ruff check src tests
uv build
```

The project disables `genlayer-test`'s automatic pytest plugins because contract checks run through owned worker processes. Browser checks use `npm ci` and `npx playwright install chromium`. Use `npm run verify:onboarding -- --connection-fixture --report-fixture` to check connection timing, credential lifecycle and report behavior with synthetic browser responses and no running Lab. Use `npm run verify:onboarding` for guided authoring checks; add `-- --live` to create an actual local Studio run with a scripted reference. Set `LAB_DATA_DIR` and `LAB_URL` when using a nondefault installation. `-- --report=RUN_ID` checks a saved passing report without creating another run; add `--unsafe` for an expected failing report.

[Architecture](docs/ARCHITECTURE.md) · [Verification](docs/VERIFICATION.md) · [Publish the setup page](docs/SETUP_PAGE.md) · [Third-party notices](THIRD_PARTY.md)
