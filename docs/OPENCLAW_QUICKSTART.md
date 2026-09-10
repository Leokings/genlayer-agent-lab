# Connect an existing OpenClaw agent

Use this after [Lab setup and dashboard access](VPS_QUICKSTART.md) work and your chosen OpenClaw agent can already answer a normal message. Keep its existing model and provider settings. This guide connects the Lab's tools; OpenClaw is optional.

## Prepare the agent

If OpenClaw was just installed in a Bash terminal and its command is missing, reload that shell's PATH:

```sh
source ~/.bashrc
openclaw --help
```

Check the Gateway in the terminal on the machine where OpenClaw runs:

```sh
openclaw gateway status
```

Use the Gateway's reported dashboard address. For a VPS, give that dashboard its own active SSH forward as well as the Lab's `8875` forward. If the Gateway listens on `127.0.0.1:18789`, forward local `18789` to remote `127.0.0.1:18789`. Saving a Termius rule is followed by starting its forwarding connection.

## Give the agent its setup prompt

1. In the Lab dashboard, choose **Record a final decision**, select the test duration, and click **Review test**. Read the expected behavior, confirm your review, and choose **Create test & connect agent**.
2. Select **MCP-compatible agent**. If OpenClaw runs on this VPS, choose **On the same computer or VPS as the Lab**, even when you view both dashboards from a laptop. Its Lab URL is `http://127.0.0.1:8765`.
3. Click **Copy agent setup prompt**, save it privately, and paste the complete message into the agent you want to test. It includes the executable path, run ID, and test key. The agent can adapt the generated `mcpServers` entry to OpenClaw's native `mcp.servers` configuration while preserving unrelated settings.

For OpenClaw on a laptop, select the SSH-tunnel location instead, use `http://127.0.0.1:8875`, and supply the local installed MCP executable path. The connector runs beside OpenClaw; Studio stays on the VPS.

Dashboard project tests allow 60 minutes for connection and runtime preparation. The full selected test duration starts once the agent has made an authenticated `observe` request and Studio is ready. Saving configuration and discovering tools do not start it. Keep the workspace administrator key and private grading material out of the agent conversation.

## Apply the connection to the running agent

The generated prompt stops at the OpenClaw restart boundary. After the agent saves its connector, run these in the **OpenClaw host's terminal**, using the account/profile that owns its Gateway:

```sh
openclaw gateway restart
openclaw gateway status
```

Then open a fresh chat with the **same named agent**. Give it the Lab's **Copy start prompt** and ask it to discover the configured Lab tools through its native tool search. It must call that connector's actual `observe` tool before carrying out the public task and finishing. Tool names can differ between hosts; use the names and schemas returned by discovery.

`openclaw mcp reload` affects only that CLI process. A successful configuration save or CLI probe does not prove the running Gateway session has loaded the tools. Repeated child-agent attempts and MCP Apps/view APIs do not repair this connection. If tools are still missing after the owning Gateway restarts, inspect the saved server, its agent scope, and Gateway diagnostics, then report the precise blocker.

In the Lab, use **Check connection** and look for **Agent activity observed**. Read the completed result under **Your tests** and save the readable report. If setup or the test expires, create a fresh reviewed test and apply its new run settings through the same Gateway/session sequence; the old run stays expired. See [first-agent results](GETTING_STARTED.md#read-the-report).
