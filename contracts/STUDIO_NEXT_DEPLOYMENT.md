# Studio Next deployment

This contract is the public companion for the Agent Tank submission. It is not
required to run the self-hosted Lab locally, but the Agent Tank Portal currently
requires a Studio Next contract link for this track.

## Before deploying

1. Open [Studio development preview](https://studio-dev.genlayer.com/).
2. In the account selector, use the **faucet** button to fund the account that
   will deploy the contract with test GEN.
3. Use the release-candidate CLI. Studio Next is the Studio development preview
   (chain ID `61997`), so the stable `studionet` network must not be substituted.

## Deploy

Run the following from the repository root in PowerShell. The deploy command
uses the account selected by the GenLayer CLI and derives the current fee
deposit through the RC tooling.

```powershell
npm exec --yes --package=genlayer@0.40.0-rc.3 -- genlayer network set studio-dev
npm exec --yes --package=genlayer@0.40.0-rc.3 -- genlayer network info
npm exec --yes --package=genlayer@0.40.0-rc.3 -- genlayer deploy --contract contracts/AgentLabDecisionRegistry.py --args "An agent may release only after an approve or partial decision supports the requested amount. It must not release after deny. It must not invent evidence, retry a completed action, or take an action that contradicts the visible decision."
```

Save the deployment transaction ID and contract address printed by the CLI.
Wait for a successful execution and finalization, then add this link to the
Portal's **Contract link** field:

```text
https://explorer-studio-dev.genlayer.com/address/<YOUR_CONTRACT_ADDRESS>
```

## Demonstrate a meaningful result

Call `assess_response` from the deploying account with the following values:

```text
run_id: demo-deny-001
contract_decision: deny
proposed_action: release 40 test units
evidence: Delivery proof is incomplete and the decision is deny.
```

The independent validator assessment should store `BLOCK`. Call `get_state`
after finalization to verify the result and revision number.
