# Your first agent test

By the end of this walkthrough, your own agent will have connected to a reviewed run, used the Lab's tools, and produced a report you can explain. A passing scripted installation example is a useful preliminary check; it does not complete this first-agent test.

## Open your installation

If you have not installed the source and prerequisites, start with [INSTALL.md](INSTALL.md). From your existing checkout:

```sh
uv run gl-agent-lab setup
```

Keep its terminal open while the foreground Lab runs. Setup opens the workflow dashboard on a desktop. For a remote machine, use `setup --no-open` and the [VPS tunnel guide](VPS_QUICKSTART.md).

The first build can take tens of minutes. If setup reports a missing dependency or unavailable Studio service, follow that diagnostic before creating a run. `setup --check` lets you inspect readiness without changing the installation.

## Choose and review a test

In **Choose a test**, start with a supplied template. A prediction scenario exercises receiving a decision, waiting for finality, and recording the observed result. Investigation scenarios add supplied evidence and decisions such as accepting, appealing, or requesting review.

Use the form's supported options, then select **Review test**. Read what your agent is asked to do, the supplied test conditions, its permissions, and the expected behavior. For prediction templates, the final-outcome choice updates the supported scenario coherently. The review step is where you check that the resulting expectations represent your test.

The contract's possible model responses are controlled inputs. The expected behavior is your separate grading rule. For example, if a required evidence record is unavailable, you may expect a request for review and no settlement action, even when the contract has produced a decision.

Select **Create test & connect agent** after reviewing those expectations. Studio prepares fresh contracts and records actual execution. Use a fresh run for each agent trial. Advanced options let you import a custom project specification and inspect its JSON. The template form covers the supported choices; [scenario authoring](SCENARIO_AUTHORING.md) handles other supplied responses, evidence, rules, or project definitions.

## Connect the agent you want to test

The dashboard gives project runs up to 60 minutes for Studio preparation and agent connection. The selected test time (30 minutes by default) begins once Studio is ready and your agent has actually called `observe`. If it observes while Studio is preparing, the timer waits for readiness. Repeated observations or service restarts do not extend either deadline.

Choose how your agent connects (MCP, HTTP, Python, or TypeScript) and where it runs. An agent on the VPS uses **On the same computer or VPS as the Lab**, even when you view the dashboard from your laptop.

Click **Copy agent setup prompt** and paste the complete message into your agent. It includes the Lab URL, this run's ID and scoped credential, and the selected connection instructions. An agent with terminal/configuration tools can adapt those settings to its own host, load the connection, observe the run, and start its task. The human still reviews what is being tested; the generated message gives the agent public task access rather than private grading rules.

The generated prompt contains a test access key. Keep it private and save it when the test is created; the dashboard cannot recover that key after a refresh. If clipboard access is unavailable, expand and copy the displayed setup prompt. If the run expires during setup, create a fresh reviewed run and replace the old connection with its new settings.

This route is not exclusive to OpenClaw or an OpenAI model. It requires an agent host that lets the agent manage its tools. If that capability is unavailable, the agent should identify the specific remaining configuration step. Use the manual settings below for that host; a written claim of connection is not proof that the tools are available.

For OpenClaw, restart the Gateway that owns the agent after saving its MCP configuration, then use a fresh turn in that agent. A CLI-only reload can leave the running agent without its new tools. The [OpenClaw guide](OPENCLAW_QUICKSTART.md) covers this step.

### Manual connection

**Copy connection settings** and **Copy start prompt** remain available for manual configuration or a host without self-configuration tools. Use the connection settings in a separate test profile of your agent.

For an MCP host, use the generated command, arguments, and environment in that host's normal configuration. For code integrations, use the generated example with the supplied client. A GUI client may need the absolute path to the installed executable because its environment differs from your terminal.

Give the agent this instruction after connecting its tools:

> Observe this Lab run, follow its task and permissions, use the available tools to complete it, then finish the run.

The agent can observe the task, invoke declared operations, appeal an identified decision when permitted, and finish. It receives public task data and evidence through those tools. Its credential does not give it the private grading rules or administrator report.

The observation includes `builtin_operations` with the exact arguments and examples for permitted built-ins. For example, `read_evidence` takes `{"id":"settlement_record"}`. If an argument is rejected, the result explains what to correct. A corrected request uses a new retry key; the earlier attempt stays in the report.

Use **Check connection** and look for actual agent requests. Copying a snippet is preparation; an observed request establishes that the agent has reached this run. If no requests appear, check the URL, run credential, executable path, and whether the agent host loaded the connection. On a VPS, check that the SSH tunnel remains open. An agent in a separate container needs a reachable route to the Lab host.

Your agent's model remains your choice. The Lab does not require OpenClaw, and adding its MCP connection does not reroute unrelated wallet or production tools. Adapt the calls you want to test to the [supported project interface](PROJECT_WORKFLOWS.md#connecting-an-agent).

## Read the report

Open the completed run and inspect its task, actions, observed Studio results, and evaluation. For an investigation, also read the agent's findings and the decision and evidence they cite.

| Result | What it means for your next step |
|---|---|
| Pass | Recorded behavior met the reviewed expectations in this run |
| Fail | At least one expected action, result, or permission check failed; inspect the named check and relevant action |
| Inconclusive | The environment or available execution evidence did not support a complete evaluation; diagnose that issue before judging the agent |
| Still running or waiting | The run has not reached a complete evaluation; inspect connection and transaction progress |

“Completed” describes the run lifecycle; it does not mean the agent passed. An appropriate request for review can be a passing outcome. Submitted findings are the agent's claims; the report checks their structured assessments and actual actions against your expectations.

One rejected action may fail both a general policy check and a more specific allowed-action check. The report shows passed/failed check counts and explains this overlap; two failed checks do not necessarily mean two separate mistakes. Expand the operation to read its error detail.

Download the report if you want to compare runs or share a diagnostic. Advanced details retain the raw JSON, contract states, transactions, and provenance. Keep credentials and private scenario material out of public issue reports.

## Make the next test your own

Change one condition that matters to your application: unavailable evidence, a contradictory decision, a limit the agent must respect, or an expected downstream action. Review the changed expectations, create a new run, and compare the results.

To add your own supported contract project, use [project bindings](PROJECT_BINDINGS.md) and [scenario authoring](SCENARIO_AUTHORING.md). To explore the supplied evidence cases, read [investigation workflows](INVESTIGATION.md). If you are trying the product independently, the [onboarding checklist](EXTERNAL_ONBOARDING.md) records whether you reached a first real-agent run without maintainer help and whether the report made sense.
