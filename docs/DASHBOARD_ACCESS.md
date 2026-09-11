# Guided dashboard access

The default owner journey is the public setup prompt, a guided local/VPS opening page, and **Connect this browser**. The owner pastes the generated sign-in request into their setup agent. The agent approves it through the installed local CLI. This does not create a run or connect a tested agent.

The setup agent teaches the connection steps directly for the owner's chosen app. The public Vercel page is a visual companion with Termius fields and a PowerShell command builder. It does not contact the VPS, receive credentials, execute commands, or claim that it configured the connection. The loopback Lab remains private behind the owner's existing connection. Numeric settings are shown only when needed; generated commands require validated server details.

## Browser approval boundary

- Requests contain a random identifier, a 12-character owner approval code, and a separate random claimant secret. Only the code enters the copied agent request. The claimant secret remains in the requesting page's memory.
- The original installation administrator credential is required to approve. Run credentials and issued browser sessions cannot approve another browser. The local CLI verifies the installation with a fresh HMAC health challenge before sending any credential; it never initializes a missing installation or follows redirects.
- The claimant must present its original secret after approval. Claim consumption is atomic and one-time; guessing an approval code or knowing the identifier is insufficient to claim.
- A successful claim issues an independent owner browser credential, not the saved installation key. It authorizes the normal owner dashboard routes for up to 24 hours. Requests expire after ten minutes. Each process holds at most 20 pending requests and 20 sessions.
- Codes and credentials are hashed in the bounded in-memory store. Pairing endpoints require loopback Host and same-origin (or absent Origin for the CLI) requests. Normal success, validation, authentication and capacity responses are non-cacheable. Nothing secret is placed in a request URL.
- State is held by one Lab server process. Restarting the Lab invalidates requests and browser sessions; multi-worker serving is unsupported. Closing the tab loses a pending request. Existing private workspace-key login and desktop fragment login remain compatible.

Owner approval is separate from agent evaluation. A setup agent must approve only an owner-supplied request and must not inspect private grading to complete sign-in. The owner still reviews a test and gives its separate connection prompt to the agent under evaluation.

## Verification

Run `uv run pytest tests/test_dashboard_access.py tests/test_dashboard_cli.py tests/test_setup.py tests/test_setup_resources.py` for expiry, replay, wrong-claimant, capacity, concurrency, route boundaries, original-owner approval and setup handoff coverage.

Run `npm run verify:dashboard-access` for a real browser against a temporary Lab and the real owner CLI. It exercises private approval, automatic browser sign-in, an unpaired browser remaining locked, clipboard fallback and mobile layout. No model or Studio execution is required. `npm run verify:setup` covers the public opening wizard and its copy buttons.

These checks establish the implemented flow. A fresh independent VPS-user walkthrough is still required before claiming that it resolves every onboarding obstacle.
