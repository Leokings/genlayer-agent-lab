# GenLayer Agent Lab

Test how your agent uses GenLayer decisions before relying on it in an application. Run a reviewed scenario in your own local Studio environment, connect your agent, and see which decisions and actions met your expectations.

[Get started](docs/GETTING_STARTED.md) · [Install](docs/INSTALL.md) · [Use a VPS](docs/VPS_QUICKSTART.md) · [Source](https://github.com/Leokings/genlayer-agent-lab)

**Version 0.1.0a12 — development candidate.** See [build status](docs/BUILD_STATUS.md), the dated [verification record](docs/VERIFICATION.md), and the [build audit](docs/BUILD_AUDIT.md) for completed and open gates.

## Start here

An agent with terminal and configuration access can handle setup for you. Paste this into your agent on the computer or VPS where you want the Lab to run:

> Set up GenLayer Agent Lab on this machine using https://github.com/Leokings/genlayer-agent-lab/blob/main/skills/setup-genlayer-agent-lab/SKILL.md. Reuse my existing installation if present. Check the requirements, start the Lab, help me open its dashboard, and guide me through reviewing my first test. Then help me use the Lab's generated agent setup prompt to connect the agent I want to test.

After you review and create a test, the dashboard provides **Copy agent setup prompt**. Paste that message into the agent you want to test: it includes the connection settings and asks the agent to configure its tools and begin. This works with agents that can manage their tool configuration; a chat-only client may still need its owner's connection settings changed. Manual connection instructions remain available.

Dashboard project runs allow up to 60 minutes for preparation and connection. The selected test time starts once Studio is ready and the agent has actually called `observe`. Saving connector settings alone does not start it.

For terminal installation:

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and a running Docker engine with Compose and Linux x86-64 containers. Setup uses Python 3.12. See [installation](docs/INSTALL.md) for platform details and resource guidance.

```sh
git clone https://github.com/Leokings/genlayer-agent-lab.git
cd genlayer-agent-lab
uv sync --locked --python 3.12
uv run gl-agent-lab setup
```

`setup` checks prerequisites, prepares or starts this installation's owned project Studio stack, starts the Lab, and opens the workflow dashboard. The first build downloads pinned dependencies and can take tens of minutes. Keep the setup terminal open while its foreground Lab is running. Use `setup --check` when you want a read-only readiness check.

Already installed? Return to the same directory and run `uv run gl-agent-lab setup` again. On a server, use `setup --no-open` and the [SSH tunnel instructions](docs/VPS_QUICKSTART.md).

## Run your first test

1. **Choose a test.** Select a supplied template, or import a custom project specification under Advanced.
2. **Review test.** Read its task, supplied conditions, permissions, and expected behavior.
3. **Create test & connect agent.** Select the connection method and where your agent runs, then use **Copy agent setup prompt** and paste it into that agent. The message asks it to configure the supported connection and start the test. Manual MCP, HTTP, Python, and TypeScript settings remain available.
4. **Check connection.** Confirm actual agent requests, then inspect its actions and the readable report. Detailed JSON is available under Advanced.

The [first-run walkthrough](docs/GETTING_STARTED.md) explains each step. A scripted example can check your installation without a paid model; testing your own agent uses that agent's model and tool integration. No OpenClaw installation is required.

## What you can test

[Project workflows](docs/PROJECT_WORKFLOWS.md) support declared typed operations and results, multi-file contracts, defined effects across contracts, actual local appeals, observed fee accounting, and recovery after a Lab interruption with Studio intact. The developer supplies possible contract model responses and separately reviewed grading rules.

[Investigation scenarios](docs/INVESTIGATION.md) test missing, stale, conflicting, or misleading evidence. They record the agent's findings, its proposed response, the decision it investigated, and the actions it actually took. [Scenario authoring](docs/SCENARIO_AUTHORING.md) explains how to adapt a template or use your own authoring agent.

The dashboard provides supported template forms and custom-specification import. The Lab tests agent behavior in the supported local environment; contract-model judgment quality and public-network settlement are outside scope. Your agent needs to use the Lab interface for the actions under test.

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

The original 18 GLSim scenarios, `service_release`, and isolated custom-contract worker remain available as advanced compatibility paths. Their commands and evidence boundaries are documented in [the examples](examples/README.md), [legacy Studio workflows](docs/STUDIO_WORKFLOWS.md), and [custom contracts](docs/CUSTOM_CONTRACTS.md). They are separate from the primary project Studio setup above.

## Development

```sh
uv run pytest -q
uv run ruff check src tests
uv build
```

The project disables `genlayer-test`'s automatic pytest plugins because contract checks run through owned worker processes. Browser checks use `npm ci` and `npx playwright install chromium`. Use `npm run verify:onboarding -- --connection-fixture --report-fixture` to check connection timing, credential lifecycle and report behavior with synthetic browser responses and no running Lab. Use `npm run verify:onboarding` for guided authoring checks; add `-- --live` to create an actual local Studio run with a scripted reference. Set `LAB_DATA_DIR` and `LAB_URL` when using a nondefault installation. `-- --report=RUN_ID` checks a saved passing report without creating another run; add `--unsafe` for an expected failing report. `npm run verify:dashboard` checks the older compatibility dashboard.

[Architecture](docs/ARCHITECTURE.md) · [Verification](docs/VERIFICATION.md) · [Third-party notices](THIRD_PARTY.md)
