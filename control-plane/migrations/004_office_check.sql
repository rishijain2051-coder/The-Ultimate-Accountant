-- Every 20 minutes, Supabase calls the control plane's office check (cp/push.py), which tells
-- admins when the office server has been silent for 3 hours in office hours. The endpoint
-- needs no secret. Only for the live schema, not the tests' or a dev one, and only when
-- PUBLIC_ORIGIN is set: cp/migrate.py puts it in place of {{PUBLIC_ORIGIN}} below. When the
-- control plane moves to another address, schedule it again there (same job name replaces it).
do $$
begin
  if current_schema() = 'office' and '{{PUBLIC_ORIGIN}}' <> '' then
    create extension if not exists pg_cron with schema pg_catalog;
    create extension if not exists pg_net with schema extensions;
    perform cron.schedule('office-check', '*/20 * * * *',
      $job$ select net.http_get('{{PUBLIC_ORIGIN}}/api/cron/office-check') $job$);
  end if;
end
$$;
