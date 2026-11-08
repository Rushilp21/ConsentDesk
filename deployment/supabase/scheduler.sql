-- Run in Supabase SQL Editor after setting the same CRON_SECRET in Vercel.
-- This template contains no real credentials. A private ready-to-run copy can
-- be generated locally with scripts/prepare_cloud_setup.py.
create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;
create extension if not exists supabase_vault with schema vault;

do $setup$
declare
  secret_id uuid;
begin
  select id into secret_id from vault.secrets where name='consentdesk_cron_secret' limit 1;
  if secret_id is null then
    perform vault.create_secret('REPLACE_WITH_CRON_SECRET','consentdesk_cron_secret');
  else
    perform vault.update_secret(secret_id,'REPLACE_WITH_CRON_SECRET');
  end if;
  select id into secret_id from vault.secrets where name='consentdesk_base_url' limit 1;
  if secret_id is null then
    perform vault.create_secret('https://consent-desk-tan.vercel.app','consentdesk_base_url');
  else
    perform vault.update_secret(secret_id,'https://consent-desk-tan.vercel.app');
  end if;
end
$setup$;

-- The endpoint enqueues each weekday/sector/mode once after 07:00 IST and
-- advances up to three leased steps per request. It also finishes user jobs
-- after a browser closes. Re-running schedule updates this named job.
select cron.schedule(
  'consentdesk-worker',
  '* * * * *',
  $cron$
    select net.http_get(
      url := (select decrypted_secret from vault.decrypted_secrets where name='consentdesk_base_url' limit 1) || '/api/cron',
      headers := jsonb_build_object('Authorization','Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name='consentdesk_cron_secret' limit 1)),
      timeout_milliseconds := 290000
    );
  $cron$
);

-- Verify scheduling: select jobid,jobname,schedule,active from cron.job;
-- Inspect delivery: select status_code,error_msg,created from net._http_response order by created desc limit 10;
-- Stop if needed: select cron.unschedule('consentdesk-worker');
