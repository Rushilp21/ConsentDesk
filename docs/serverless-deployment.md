# One public URL: Vercel + Supabase

The repository root now contains a Flask Vercel Function, a static UI builder,
PostgreSQL storage and leased research jobs. Oracle/Render is not required.
Local `start.ps1` still supports SQLite. Cloud configuration is not yet deployment
proof; complete the steps below and test the actual production URL.

## 1. Supabase connection

Open [Supabase dashboard](https://supabase.com/dashboard), select **ConsentDesk**,
click **Connect**, and choose **Transaction pooler** (port **6543**). Copy the
PostgreSQL connection string from your actual project:

```text
postgresql://postgres.PROJECT_REF:PASSWORD@YOUR_POOLER_HOST:6543/postgres
```

Replace PASSWORD with the database password chosen at project creation. URL-encode
special characters in that password. Project name is not project reference.
Do not substitute an anon/service-role API key. Keep the full URL private.

The backend creates private `cd_shared`, `cd_accounts` and `cd_user_*` schemas
automatically. Do not expose them through Supabase's browser Data API. Local
SQLite data remains on your computer; new hosted accounts start fresh.

## 2. Private setup files

Already generated locally; repeat without rotating the saved secret using:

```powershell
python scripts/prepare_cloud_setup.py
```

Edit **`.env.vercel`** in the repository root and fill **DATABASE_URL**. It already
contains a random **CRON_SECRET**, the production origin, polling settings and
your existing broker token when configured. Both this file and the generated
`output/supabase-scheduler.private.sql` are ignored by Git. Never share them.

The existing public-launch gate remains **PUBLIC_DATA_APPROVED=false**. Set it
to **true** only after confirming the existing public data-display/recommendation
requirements. Otherwise the API deliberately reports setup incomplete. This is
separate from payment/hosting setup. AI credentials are optional. This migration
preserves the existing OpenAI integration; it does not add Gemini.

## 3. Vercel settings and environment

Open [ConsentDesk settings](https://vercel.com/rushilrp21-gmailcoms-projects/consent-desk/settings).
If the team slug differs, open [Vercel dashboard](https://vercel.com/dashboard)
and select ConsentDesk.

| Setting | Value |
| --- | --- |
| Root Directory | Repository root: clear `deployment/vercel` |
| Framework | Other |
| Build Command | `python scripts/prepare_serverless.py` |
| Output Directory | `public` |

Build/output are also in root `vercel.json`. Remove conflicting old dashboard
overrides. In **Settings → Environment Variables**, import **`.env.vercel`** for
**Production**. If import is unavailable, add its named values individually.
Do not prefix any secret with `VITE_` or `NEXT_PUBLIC_`.

Redeploy the latest main commit. The Python Function needs the configured
300-second duration; use Fluid compute and verify your project's limits.
The root `/api/*` rewrite reaches Python in the same deployment. **No external
backend URL needs pasting.** The stable URL stays
`https://consent-desk-tan.vercel.app`.

The old `deployment/vercel` folder remains a legacy separate-host package and
still has an `.invalid` rewrite. Do not use that folder as the Vercel root.

## 4. Hosted worker and daily scans

In Supabase, open **ConsentDesk → SQL Editor → New query**. Paste the contents of
the private local **`output/supabase-scheduler.private.sql`** and run it once.
This stores the matching CRON_SECRET in Supabase Vault and creates one named
schedule. Re-running updates it. Never commit the private SQL file.

The schedule calls `/api/cron` every minute. The authenticated endpoint enqueues
each weekday/sector/mode once after **07:00 IST** and processes up to three leased
steps per call. Each stock is a checkpoint. Old briefs remain until successful
replacement. Completion time depends on provider availability and quotas; all
scans are not guaranteed to finish exactly at 07:00. Personal jobs continue if
the browser closes.

The Vercel daily cron is a fallback only: Hobby timing is coarse and a single
call cannot finish every scan. Configure the Supabase worker for unattended
completion. If Vercel deployment protection blocks it, configure access for the
production endpoint. Do not commit protection-bypass credentials.

```sql
select jobname, schedule, active from cron.job;
select status_code, error_msg, created
from net._http_response order by created desc limit 10;
```

The generated SQL also includes a command to stop the schedule if necessary.

## 5. Verify

1. Production `/api/health` must return `{"status":"ok"}`.
2. Register, sign in, run one-stock research, refresh, and confirm it remains in
   Saved research. Check login/logout and two-account isolation.
3. Check actual quote timestamps and broker connectivity. Quotes poll every 15s;
   no permanent broker WebSocket runs in Functions.
4. Confirm scheduler deliveries have HTTP 200 and queued jobs advance. Test a
   sector refresh and retention of the previous brief through a failed refresh.

Health alone does not verify broker access or research. The schemas initialise
on the first correctly configured API request.

## Runtime limits

- Durable: accounts, sessions, budgets, research, daily briefs, job checkpoints,
  paper journals and alerts are in PostgreSQL. Workspace transactions are isolated.
- Leases prevent concurrent duplicate work. A killed request can resume after
  the 330-second lease expires. Function `/tmp` is only a disposable cache.
- Alerts and paper monitoring refresh while a browser is active or via Refresh
  desk. There is no permanent 10s monitor while everyone is offline.
- Free hosting/database/API limits apply. Supabase may pause inactive free
  projects. This is a small-family deployment, not a load-tested public service.
- The SQLite backup script remains local-only. Use PostgreSQL export/backup
  procedures for hosted data.

The migration is tested locally against SQLite and an isolated TLS PostgreSQL 16
instance. Live Supabase/Vercel checks still require your environment values,
redeployment and scheduler SQL.

References: [Vercel Python](https://vercel.com/docs/functions/runtimes/python),
[Supabase pooler](https://supabase.com/docs/guides/database/connecting-to-postgres),
[Supabase scheduling](https://supabase.com/docs/guides/functions/schedule-functions).
