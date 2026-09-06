# Verification record

Verified on 2026-09-05 and 2026-09-06, native Windows x64, isolated Python 3.12.13, Node 24.13.0.

## Alpha 3 local Studio verification — September 6

- The complete non-Studio-live suite passed **379 tests**. Later relay/stack
  checks passed **48 tests**, and the final idle-vote correction passed **39
  adapter/conformance tests**. These focused counts overlap the full suite.
  Ruff passed. The only suite warning was upstream Starlette/AnyIO deprecation.
- Built the pinned Studio v0.121.6 source at commit
  `366f085a479bb9e6028ce326c2c13f798a9752c7`, including its upstream license.
  Actual image: `sha256:0fdb3c62ee910b1f4ef98080cc2a766f5a22a0ebccc973107beaedca280d02a6`.
  Startup, actual runtime inspection and SDK doctor passed with chain 61999
  and a 30-second local finality window.
- **Three real Studio tests passed in 254.51 seconds**, using GenVM v0.2.16
  and fixture-only validators. Separate fresh contracts returned approve and
  deny. Both recorded successful execution, ACCEPTED and FINALIZED checkpoints.
  Approve execution: `0x3a4c4ec756c3197e7ce0815ab639641de1b0b7fd83af4c2909fa628db8cd2d90`.
  Deny execution: `0x89e4cd6f0c2597e3372666c4559c6c7b20a9df24033d80c13f976a7c16504707`.
- An actual appeal completed a new `Validator Appeal Failed` round and upheld
  the approved result, followed by successful finalization. This is a passing
  appeal-lifecycle test, not a claim that the challenge changed the decision.
  Execution: `0xd1b2cfeacac27c53ca733b7e68f6e37ca8ec80e3978c74fd0359925b02d02567`.
  Global validators were not changed during the tests. Evidence is in the
  installation's `.lab/studio-live-*.json` files.
- The real Studio worker failed an external TCP connection probe as intended;
  actual network membership, immutable images, loopback publication, resource
  limits and owned volumes passed inspection. A fixed-target relay solves this
  Docker engine's inability to publish a port on an internal network.
- Alpha 3's custom GLSim worker rebuilt and passed its real contract probe:
  `sha256:a643dd7ebaf2f5e84c190f1f242c6b3c547db7feb4d4a3599b8f9b592ca2ba3d`.
  Its staged CLI progress and structured diagnostics were exercised.
- Dashboard launch, independent grades, comparison, HTML download and mobile
  layout passed without page errors. The new runtime selector is present.
- Independent Python, TypeScript and MCP stdio clients each passed the custom
  delivery contract through the running Lab HTTP service with `backend=studio`.
  All four independent grades passed. Run IDs, in that order:
  `92d6227bb6634b4d862ceaa64dbfa65e`, `717da87a158043178d81cf24ec91a486`,
  `f763a213189f49a9ae96b4169d4ee136`. The dashboard also displayed the actual
  Studio report and its four passing grades without page errors.
- The alpha 3 wheel installed into the separate verification environment,
  reported version 0.1.0a3, and verified the running Studio configuration and RPC.
- `studio down` followed by `studio up` passed. Startup took 32.2 seconds,
  reused the same image/cache and preserved the finalized appeal transaction.
  The Lab's run history and administrator token were retained.

Model responses and protocol balances remain fixtures/simulations. Agent
scenario events remain scripted; only the separate Studio checkpoints above
establish actual local consensus observations. Modern bond accounting and
public-network finality are unsupported by this stable integration.

## Alpha 2 live Docker verification

Docker Desktop's Linux engine is now working (Engine 29.1.5, x86_64). The startup failure was traced to stale AF_UNIX socket objects that survived the user's factory reset. Quarantining the two affected runtime directories allowed Docker to recreate its endpoints. See [troubleshooting](TROUBLESHOOTING.md). The lab's database and historical reports were preserved.

- Final complete suite: **204 tests passed, zero skipped**, with one upstream Starlette/AnyIO deprecation warning. Ruff passed.
- The pinned worker image built and passed its real bundled-contract readiness probe. Image ID: `sha256:331b8c75dab3a41b931c4660db39e8f42f7a89ccca6736cb73c2ecc4473f900d`.
- Live `DeliveryAssessment.assess_delivery` tests passed for its structured JSON result, immutable source/binding hashes, payment effect and report persistence. A deliberate deny response against an independently expected approval correctly failed the decision grade while the agent safely withheld payment.
- All **18/18 scenarios** passed with the imported delivery binding and the safe reference agent through the real HTTP service and Docker worker.
- Independent Python, TypeScript and actual MCP stdio clients each passed `escrow-normal` using the custom Docker contract. Run IDs: `f83274e680564101a169954b957c47c0`, `b9cb12e0a7ba4590bc036e4e487c9238`, `c4f31f40aaf44d179ecd4e8dc7edb7d1`. The repeatable verification script is `scripts/verify-custom-clients.py`; it retains concise local evidence without credentials.
- Three actual container-boundary tests passed: approve/deny/approve in fresh containers; successful denial after blocked network access; and a runaway method that wrote an entry witness before its 10-second timeout. Each test independently verified its exact container IDs and ownership labels were absent afterward.
- The custom-contract dashboard path now completes successfully. Scenario launch, four grades, findings, comparison, HTML download and 390-pixel mobile layout passed without page errors.
- The rebuilt wheel was installed in the separate verification environment and passed a custom delivery-contract run (`63a71dc6c455403eaab0ea8d61607c76`) with all four grades passing.
- Two defects were fixed before building: GLSim 0.29.2's empty proxy-class method schema (validate the bound method instead), and copied files being unreadable by a nonroot worker under a restrictive Linux builder umask (explicit image ownership).

These alpha 2 checks established the Linux worker running under Docker Desktop
on Windows. Studio was verified later in alpha 3 as recorded above. Separate
Linux/macOS host installation CI remains unverified.

## Initial alpha 2 checks before Docker repair

- Final Python suite: **187 passed, 3 skipped** (the live-container cases), with one upstream Starlette/AnyIO deprecation warning.
- Custom bindings: confined paths (including Windows paths and symlinks), byte/structure limits, alias rejection, literal-null arguments, exact result mapping, canonical snapshots and hash validation passed.
- Custom engine runs through an injected test evaluator passed API/CLI/Python/TypeScript/MCP integration checks. These establish routing and grading, not actual Docker execution.
- Re-import preserves queued contract snapshots. Independent decision/behavior/outcome/completion grades remain in force. Missing Docker fails closed with an inconclusive result; no native fallback exists.
- Schema 1 migration preserved all 25 existing demo runs and produced a pre-migration SQLite backup. A failed startup now releases its data-directory lock, allowing a repaired installation to reopen.
- Cancellation signals preparing container jobs. Controlled Docker-process tests verified creation flags, image-ID dispatch, ownership checks, cleanup, output limits and cancellation. Actual child processes verified timeout/output limits and termination of a spawned grandchild that inherited pipes.
- `DeliveryAssessment` sample: GenVM lint and contract validation passed (one public write and one view).
- Actual dashboard: custom binding selection sent the correct backend and binding ID. Missing Docker produced an inconclusive report containing the snapshot hashes. Bundled unsafe-test launch, four grades, comparison, HTML download and mobile layout passed with no page errors. The custom report's collapsed provenance panel required the browser test to read `textContent` rather than rendered `innerText`.
- Packaged alpha 2 installed in a separate environment, imported the delivery binding and completed an actual bundled GLSim escrow run (`720c51b6997b409d90723cc9d660c14f`) with all four grades passing.
- Three actual-container tests exist for approval/denial/clean state, blocked network access and runaway code. They are explicitly skipped here because Docker's API times out. Docker Desktop processes were present and WSL Linux startup succeeded; a hardware-virtualization failure was not established. No Docker reset, prune or unrelated container removal was performed.
- CI now includes a Linux Docker image build, readiness probe, actual-container tests and the full bound scenario suite. That CI job has been authored, not executed here.

The Docker gate was unresolved at that point; the later live checks above supersede that limitation.

## Alpha 1 executed checks

- Full Python suite: **135 tests passed**. One upstream Starlette/AnyIO deprecation warning; no test failures.
- After freezing final report snapshots: **91 engine/CLI tests passed**.
- Ruff: all checks passed.
- Bundled intelligent contract: `genvm-lint` passed.
- Actual runtime: approve and deny execution, three validator callbacks/four fixture model calls, clean repeated state, malformed model response rejection, worker failure handling and Unicode-path cleanup.
- Actual local service: **18/18 bundled scenarios passed** using the safe scripted reference and GLSim. Unsafe and refusing controls fail their intended checks; they are not expected to pass.
- Independent integrations: Python, TypeScript and actual MCP stdio each completed `escrow-normal` against the public HTTP service and actual GLSim. Additional integration coverage includes provisional approval, revised denial and lost acknowledgement.
- Browser: authenticated dashboard, 18 scenario options, launching an unsafe test, four grades, findings, report comparison and HTML download passed. Desktop and 390-pixel mobile layouts inspected; no page exceptions or horizontal page overflow observed. The history table scrolls horizontally on narrow displays.
- Setup skill passed its structure validator.
- Source distribution and wheel built. A separate Python environment installed the wheel, passed `doctor`, and completed an actual GLSim escrow run (`3e484895b041441eba6aa03dea02ee19`).
- A custom YAML case for an obsolete policy imported and passed its safe-reference fixture test.

## Reproduced and fixed defects

- Upstream Windows open-file deletion: worker-local deferred cleanup, no installed package edits.
- Missing NumPy requirement in the simulator's installed extra: explicit pinned dependency.
- HTTP validation bypassing behavior grading: authenticated malformed actions now produce sanitized rejection events.
- Lost acknowledgements passing without reconciliation: completion requires resolving the uncertain result with the same action key.
- Historical report drift: originating version and completed report snapshot are persisted. Legacy traces infer recorded acknowledgement reconciliation.
- Newer database schema silently downgraded: unsupported schema versions are rejected before schema mutations.

## Not established by these checks

Linux/macOS host CI has been authored but not run on this Windows host. The examples are scripted agent controls, not tests of live LLM reasoning. No real funds, live provider API calls, public-network finality, modern appeal bond settlement, automatic startup installation or external developer onboarding were tested.

The SDK archive was checked against the pinned official release hash; it is cached outside this repository. Native execution checks run bundled trusted code. The live container checks exercise specific isolation and resource-limit behavior; they are not a general security certification or independent attestation.
