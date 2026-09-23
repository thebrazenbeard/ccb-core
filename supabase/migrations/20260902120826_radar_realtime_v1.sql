-- Radar V1 selective Postgres Changes publication.
-- Publish low-volume control-plane projections only; high-volume messages and
-- telemetry remain durable/queryable but are not streamed through Postgres Changes.

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'nodes',
    'assignments',
    'dependencies',
    'acknowledgements',
    'reconciliation_events'
  ]
  loop
    if not exists (
      select 1
      from pg_publication_tables
      where pubname = 'supabase_realtime'
        and schemaname = 'radar'
        and tablename = table_name
    ) then
      execute format('alter publication supabase_realtime add table radar.%I', table_name);
    end if;
  end loop;
end
$$;
