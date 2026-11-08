# Free backend hosting

**Updated recommendation:** use the repository-root Vercel + Supabase mode in
[serverless-deployment.md](serverless-deployment.md). The always-running-host
comparison below applies to the legacy Docker deployment, not the new mode.

ConsentDesk's current deployment keeps the static frontend on Vercel and runs
Python separately. The backend needs persistent SQLite storage and a running
process for its scheduler, research jobs and broker connection.

| Option | Suitability for this application |
| --- | --- |
| Oracle Cloud Always Free VM | My recommended free route for the existing Docker setup: eligible compute and persistent block storage within account limits. Requires Linux/server administration. Capacity may be unavailable; idle instances can be reclaimed. |
| Render Free web service | Suitable for a disposable demonstration. Sleeps after 15 minutes without inbound traffic and loses local SQLite data on restart/redeploy/spin-down. Cannot reliably run the unattended 7 AM scan. |
| Railway Free | Currently offers $1 monthly credit after the initial trial. Treat as a limited allowance, not a promise of enough resources for this continuously running backend. |

The existing `render.yaml` selects **paid Starter compute and a disk**. It is not
a free Render template. Using a hosted database would require code changes;
adding a Postgres URL does not migrate this SQLite application automatically.

For a VM, use the existing Docker and HTTPS instructions in
[release-and-deployment.md](release-and-deployment.md). Keep backend credentials
on that host. Once its real HTTPS origin works, generate the Vercel connection:

```powershell
python scripts/prepare_vercel.py --backend https://YOUR-ACTUAL-BACKEND --verify
```

Commit the regenerated `deployment/vercel` files and redeploy Vercel. The present
rewrite remains `backend.example.invalid` until a real backend is hosted.
This document does not create or deploy a cloud service.

Official hosting limits checked 8 November 2026:
[Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[Render Free](https://render.com/docs/free),
[Railway Free/trial](https://docs.railway.com/pricing/free-trial).
