# Working release and Vercel deployment

**Legacy separate-host guide.** For the new single-URL Vercel + Supabase
deployment, use [serverless-deployment.md](serverless-deployment.md).

The configured Upstox credential successfully returned an authenticated quote, a connected WebSocket with packets, 270 daily candles, news and a 122-row Nifty option chain during read-only verification on 3 November 2026. Market status was CLOSING_END; live-session alert behaviour still needs a market-hours check. No credential is included in release artifacts. The optional language-model key/model remain unconfigured; deterministic research and the chart learner work without them.

## Deployment arrangement

Deploy the static dashboard/chatbot to Vercel and keep the Python backend on an always-running host with a persistent disk. The current application uses local SQLite accounts/journals, background jobs, a daily scheduler and an ongoing broker connection. This release does not migrate those services into Vercel Functions or temporary storage.

1. Deploy the backend with Docker and persistent `data/`, using the existing HTTPS/public-mode deployment guide. Configure Upstox credentials only on that backend. Verify data-display/recommendation permissions before public launch.
   A managed-hosting Blueprint and exact setup steps are in [Render deployment](render-deployment.md).
2. Generate the Vercel frontend using your actual HTTPS backend hostname:

   ```powershell
   python scripts/prepare_vercel.py --backend https://YOUR-BACKEND-HOST --verify
   ```

3. Deploy `deployment/vercel` on Vercel. Framework: Other. Build command: none. Output directory: `.`. For a Git import set Root Directory to `deployment/vercel` and commit that generated directory. `vercel.json` forwards `/api/*` to the backend; browser requests remain on the frontend origin.
4. On the backend set `PUBLIC_ORIGIN=https://YOUR-FRONTEND.vercel.app`, `PUBLIC_MODE=true`, `SECURE_COOKIES=true` and the required public-data approval setting after your permissions review. Restart. Use the exact frontend origin, without a trailing slash or wildcard.
5. Test login/logout, two-account isolation, quotes, research-job polling, daily briefs, token expiry and backup restore on the deployed hosts. Do not deploy your private `.env`, data directory or backups to Vercel.

The supplied frontend directory initially points at `https://backend.example.invalid`; regenerate it before deploying. It will not connect to your local computer from a public website. No public deployment or cloud runtime verification was performed. Hosting and a backend URL are still required; a Vercel-only conversion would require durable hosted storage and job/scheduler changes.

## Local use

Run `python -m pip install -r requirements.txt`, then `.\start.ps1` from the project directory. Your existing private `.env` remains local; the release ZIP deliberately excludes it. Keep your token private.
