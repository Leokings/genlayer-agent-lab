# Publish the setup page

The [public setup page](https://genlayer-agent-lab-setup.vercel.app) is a static copy of [docs/START.html](START.html), the canonical source. It can be opened before installing the Lab, without an account or login. Vercel hosts only this page; the Lab, Studio, MCP connector, model and database run in the user's own environment.

After editing the source, verify and prepare it from the repository root. Browser verification requires the development dependencies (`npm ci` and `npx playwright install chromium`).

```sh
npm run verify:setup
npm run prepare:setup-site
cd .lab/setup-site
vercel whoami
vercel link --project genlayer-agent-lab-setup
vercel --prod
```

Use the installed Vercel CLI and confirm the intended account and team before linking; select the correct team in the link prompt. Run the deployment commands from `.lab/setup-site`. The preparation script copies only `docs/START.html` to `index.html` and writes `vercel.json` plus a `.vercelignore` that allows only `index.html` and `vercel.json` into the upload. Repository files, Lab state and credentials are excluded.

After deploying, open the public URL anonymously and check the copy button. No automatic Git deployment is configured: changes to the source need these explicit preparation and deployment steps. The source remains available in downloaded checkouts and installation kits, and an installed Lab serves it at `/setup`.
