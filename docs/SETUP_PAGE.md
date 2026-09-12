# Publish the public website

The [public home page](https://genlayer-agent-lab-setup.vercel.app/) explains the Lab and leads to the [setup page](https://genlayer-agent-lab-setup.vercel.app/setup.html). Both work before installing the Lab, without an account or login. Vercel hosts these static pages and their favicon; the Lab, Studio, MCP connector, model and database run in the user's own environment.

Edit [HOME.html](HOME.html) for the product overview and [START.html](START.html) for the installation and dashboard-opening guide. The home page carries no installation prompts. [favicon.svg](favicon.svg) is the public icon; START embeds the same icon to remain self-contained when opened locally or served by an installed Lab. The installed dashboard's matching icon is in `src/genlayer_agent_lab/assets/favicon.svg`.

After editing the source, verify and prepare it from the repository root. Browser verification requires the development dependencies (`npm ci` and `npx playwright install chromium`).

```sh
npm run verify:setup
npm run prepare:setup-site
cd .lab/setup-site
vercel whoami
vercel link --project genlayer-agent-lab-setup
vercel --prod
```

Use the installed Vercel CLI and confirm the intended account and team before linking; select the correct team in the link prompt. Run the deployment commands from `.lab/setup-site`. The preparation script publishes HOME as `index.html`, START as `setup.html`, and the favicon. Its `.vercelignore` allows only these files and `vercel.json` into the upload. Repository files, Lab state and credentials are excluded.

New setup links should use `/setup.html`. Existing root links with setup fragments (`#set-up-lab`, `#prepare-contract-test`, `#open-dashboard`) or connection parameters still lead directly to setup and preserve their query and fragment. A plain `/` visit stays on the home page. `/setup` redirects to `/setup.html` on Vercel.

After deploying, open the public URL anonymously, follow Start to setup, check the copy button and an older dashboard-opening link, and verify the favicon. No automatic Git deployment is configured: changes to the source need these explicit preparation and deployment steps. The source pages remain available in downloaded checkouts and installation kits; an installed Lab continues serving START at `/setup`.
