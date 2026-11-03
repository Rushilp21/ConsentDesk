# Render backend + Vercel frontend

## Status

`render.yaml` prepares a Docker web service with a 1 GB persistent disk. A hosting
account must create the service before the frontend can connect. Committing this
file does not start a backend. The checked-in Vercel rewrite still uses
`backend.example.invalid` until a real service URL is available.

## Create the backend once

1. Sign in at [Deploy ConsentDesk on Render](https://render.com/deploy?repo=https://github.com/Rushilp21/ConsentDesk).
   Authorize access to the repository if asked. Use the root `render.yaml` Blueprint.
2. Review the **paid Starter compute + 1 GB disk** estimate before confirming.
   This is a small-beta starting configuration, not a load-tested capacity promise.
   Render's free tier sleeps and cannot preserve SQLite accounts and histories.
3. Render prompts for `UPSTOX_ANALYTICS_TOKEN`: copy its value from your local
   `.env` into Render's private environment field. Do not paste it into GitHub,
   Vercel's static files, or this document.
4. `PUBLIC_DATA_APPROVED` is the existing public-launch gate. Set it to `true`
   only after confirming data-display and recommendation permissions. Otherwise
   the backend deliberately refuses public startup.
5. The frontend origin is already set to `https://consent-desk-tan.vercel.app`.
   Change `PUBLIC_ORIGIN` if your stable production domain changes. Use that
   production URL to sign in; preview domains need their own configuration.
6. Wait for the service to become healthy. Copy its actual HTTPS URL from Render;
   the `consentdesk-api` name does not guarantee a particular hostname.

Optional: add `OPENAI_API_KEY` and `OPENAI_MODEL` together in Render's Environment
page to enable the language-model planner. Existing chart/news research works
without them. Local accounts/history are not uploaded; the hosted disk starts fresh.

## Connect Vercel once the backend is healthy

From the repository root, replace the URL with the real Render URL:

```powershell
python scripts/prepare_vercel.py --backend https://YOUR-ACTUAL-SERVICE.onrender.com --verify
git add deployment/vercel
git commit -m "Connect hosted backend"
git push origin main
```

Verification checks `/api/health` and public authentication mode before modifying
the frontend. Vercel remains **Other**, root **deployment/vercel**, no build
command, output **.**. Redeploy the latest commit if automatic deployment is off.
Opening the stable Vercel URL should now show registration/login rather than
the backend connection error. Test registration, login/logout, saved research
and live quote timestamps; check Render logs for scheduler and broker errors.
Health alone confirms the server, not broker credentials or fresh market data.

## Subsequent changes

Render deploys new `main` commits automatically. Vercel deploys its generated
frontend; regenerate that directory after changing `web/`. Keep one backend
instance because SQLite, the broker stream and scheduler run in that process.
The 7 AM scan uses IST in the application, independent of the host timezone.
Keep the service running overnight. Update expired broker tokens on Render.

The disk preserves hosted data across deploys. Keep private database backups and
test restoring them before relying on hosted history. Files from your local
ignored `data/` directory are intentionally not included in the Docker image.

References: [Render Blueprints](https://render.com/docs/blueprint-spec),
[persistent disks](https://render.com/docs/disks),
[free-tier limitations](https://render.com/docs/free).
