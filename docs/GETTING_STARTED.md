# Your first agent test

By the end of this walkthrough, your own agent will have connected to a reviewed run, used the Lab's tools, and produced a report you can explain. A passing scripted installation example is a useful preliminary check; it does not complete this first-agent test.

## Open your installation

Open the [setup page](https://genlayer-agent-lab-setup.vercel.app/). If the Lab is new, copy its setup prompt into your agent. If installed already, choose **Open my dashboard** and follow the guide for your computer or VPS.

When the dashboard asks you to sign in, choose **Connect this browser**, then paste **Copy sign-in request** into the agent that installed your Lab. Return to the browser after approval. You do not need to retrieve a workspace key or restart the Lab to sign in.

If you prefer the terminal, from your existing checkout:

```sh
uv run gl-agent-lab setup
```

Keep its terminal open while the foreground Lab runs. Setup opens the workflow dashboard on a desktop. For a remote machine, use `setup --no-open` and the [VPS tunnel guide](VPS_QUICKSTART.md).

The first build can take tens of minutes. If setup reports a missing dependency or unavailable Studio service, follow that diagnostic before creating a run. `setup --check` lets you inspect readiness without changing the installation.

## Choose and review a test

Choose a mode before choosing a test. **Studio execution** is the default. Its prediction templates exercise receiving a decision, waiting for observed finality, and recording the result. Investigation templates add supplied evidence and decisions such as accepting, appealing, or requesting review.

**Quick simulation · GLSim** stays in the same dashboard and offers its own escrow, treasury and generic scenarios: normal, provisional, timeout, wrong scope and duplicate acknowledgement. GLSim evaluates the supported contract with controlled inputs; the consumer timeline and application effects are scripted. These statuses do not establish GenLayer finality, and quick mode has no appeal scenarios. Studio project-v2 JSON cannot be imported or automatically converted into a quick test. Already imported compatible legacy bindings are an advanced quick-mode option using the isolated `container-glsim` backend.

The former developer-dashboard URL redirects to this dashboard. Both modes share the run history, which identifies the actual backend and preserves earlier Studio and fixture reports.

Changing the mode view does not change or stop an existing run. Continue an active run with its existing connection; choose a mode and create a new test when you want a separate trial.

Use the form's supported options, then select **Review test**. Read what your agent is asked to do, the supplied test conditions, its permissions, and the expected behavior. For prediction templates, the final-outcome choice updates the supported scenario coherently. The review step is where you check that the resulting expectations represent your test.

The contract's possible model responses are controlled inputs. The expected behavior is your separate grading rule. For example, if a required evidence record is unavailable, you may expect a request for review and no settlement action, even when the contract has produced a decision.

For Studio, select **Create test & connect agent** after reviewing those expectations. Studio prepares fresh contracts and records actual execution. Custom project import remains under **Studio execution**. In quick mode, choose a supplied scenario, select **Review quick test**, confirm the task and controlled conditions, then **Create quick test**. Prepare the agent host first: quick runs have a shorter connection window. Use a fresh run for each agent trial. [Scenario authoring](SCENARIO_AUTHORING.md) explains Studio project definitions and the separate quick-mode limits.

## Connect the agent you want to test

The dashboard gives project runs up to 60 minutes for Studio preparation and agent connection. The selected test time (30 minutes by default) begins once Studio is ready and your agent has actually called `observe`. If it observes while Studio is preparing, the timer waits for readiness. Repeated observations or service restarts do not extend either deadline.

Bundled quick simulations have a ten-minute runtime deadline beginning after runtime preparation, without the Studio connection allowance. Their timer does not wait for the first observation. Connect immediately after creating one; if it expires, review and create another run with fresh settings.

Choose how your agent connects and where it runs. Studio offers MCP, HTTP, Python and TypeScript settings; quick mode offers **MCP-compatible agent** or **HTTP / another framework**. Choose the same-computer/VPS option for an agent on the Lab's VPS, even when you view the dashboard from your laptop. Keep **My agent** selected in quick mode when testing your own agent; the explicitly labelled scripted references are installation controls.

Click **Copy agent setup prompt** and paste the complete message into your agent. It includes the Lab URL, this run's ID and scoped credential, and the selected mode's connection instructions. Every new run needs a new prompt; replace the prior scoped connection when changing runs or modes. An agent with terminal/configuration tools can adapt those settings to its own host, load the connection, observe the run, and start its task. The human still reviews what is being tested; the generated message gives the agent public task access rather than private grading rules.

The generated prompt contains a test access key. Keep it private and save it when the test is created; the dashboard cannot recover that key after a refresh. If clipboard access is unavailable, expand and copy the displayed setup prompt. If the run expires during setup, create a fresh reviewed run and replace the old connection with its new settings.

This route is not exclusive to OpenClaw or an OpenAI model. It requires an agent host that lets the agent manage its tools. If that capability is unavailable, the agent should identify the specific remaining configuration step. Use the manual settings below for that host; a written claim of connection is not proof that the tools are available.

For OpenClaw, restart the Gateway that owns the agent after saving its MCP configuration, then use a fresh turn in that agent. A CLI-only reload can leave the running agent without its new tools. The [OpenClaw guide](OPENCLAW_QUICKSTART.md) covers this step.

### Manual connection

Studio also provides **Copy connection settings** and **Copy start prompt** for manual configuration. Quick mode includes the selected MCP or HTTP settings in its complete agent setup prompt. Use the mode's settings in a separate test profile of your agent.

For an MCP host, use the generated command, arguments, and environment in that host's normal configuration. For code integrations, use the generated example with the supplied client. A GUI client may need the absolute path to the installed executable because its environment differs from your terminal.

Give the agent this instruction after connecting its tools:

> Observe this Lab run, follow its task and permissions, use the available tools to complete it, then finish the run.

For Studio, the agent can observe the task, invoke declared operations, appeal an identified decision when permitted, and finish. Quick mode uses `observe`, `request_decision`, `read_decision`, `act` and `finish`; it has no appeal tool. The generated MCP settings select `LAB_MODE=workflow` for Studio or `LAB_MODE=scenario` for quick runs. Use the supplied settings rather than changing the mode on an existing run. Run credentials do not give the agent private grading rules or administrator reports.

Studio observations include `builtin_operations` with the exact arguments and examples for permitted built-ins. For example, `read_evidence` takes `{"id":"settlement_record"}`. In either mode, follow the public observation and tool schemas. If an argument is rejected, correct it using a new retry key; the earlier attempt stays in the report.

In Studio, use **Check connection**; in quick mode, read the run's agent-observation status. Look for an actual authenticated request. Copying a snippet is preparation; an observed request establishes that the agent reached this run, not that it is still connected. If no requests appear, check the URL, run credential, executable path, and whether the agent host loaded the connection. On a VPS, check that the SSH tunnel remains open. An agent in a separate container needs a reachable route to the Lab host.

Your agent's model remains your choice. The Lab does not require OpenClaw, and adding its MCP connection does not reroute unrelated wallet or production tools. Adapt the calls you want to test to the [supported project interface](PROJECT_WORKFLOWS.md#connecting-an-agent).

## Read the report

Open a run from the unified history and check its backend and evidence note first. Inspect its task, agent actions, execution evidence and evaluation. New Studio project reports show observed Studio results; GLSim reports describe contract evaluation and scripted consumer behavior, not native appeals or protocol finality. For a Studio investigation, also read the agent's findings and the decision and evidence they cite. Earlier basic Studio scenarios can have scripted consumer timelines even though their contract backend was Studio. Older reports, including fixture runs, retain their original evidence limits and backend.

| Result | What it means for your next step |
|---|---|
| Pass | Recorded behavior met the reviewed expectations in this run |
| Fail | At least one expected action, result, or permission check failed; inspect the named check and relevant action |
| Inconclusive | The environment or available execution evidence did not support a complete evaluation; diagnose that issue before judging the agent |
| Still running or waiting | The run has not reached a complete evaluation; inspect connection and transaction progress |

“Completed” describes the run lifecycle; it does not mean the agent passed. An appropriate request for review can be a passing outcome. Submitted findings are the agent's claims; the report checks their structured assessments and actual actions against your expectations.

One rejected action may fail both a general policy check and a more specific allowed-action check. The report shows passed/failed check counts and explains this overlap; two failed checks do not necessarily mean two separate mistakes. Expand the operation to read its error detail.

Download the report if you want to compare runs or share a diagnostic. Its raw JSON retains the execution details available from that backend. Keep credentials and private scenario material out of public issue reports.

## Make the next test your own

For Studio, change one supported condition that matters to your application: unavailable evidence, a contradictory decision, a limit the agent must respect, or an expected downstream action. Review the changed expectations, create a new run, and compare the results. For quick simulation, choose another supplied scenario from its catalog; custom Studio expectations cannot be transferred into that timeline.

To add your own supported contract project, use [project bindings](PROJECT_BINDINGS.md) and [scenario authoring](SCENARIO_AUTHORING.md). To explore the supplied evidence cases, read [investigation workflows](INVESTIGATION.md). If you are trying the product independently, the [onboarding checklist](EXTERNAL_ONBOARDING.md) records whether you reached a first real-agent run without maintainer help and whether the report made sense.
