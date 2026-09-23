-- Radar control-plane V1
-- GitHub-canonical source for the rebuildable Supabase projection.

create schema if not exists radar;

revoke all on schema radar from public;
revoke all on schema radar from anon;
revoke all on schema radar from authenticated;
grant usage on schema radar to service_role;

create or replace function radar.prevent_event_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, radar
as $$
begin
  raise exception 'RADAR_APPEND_ONLY_EVENT' using errcode = '55000';
end;
$$;

create table if not exists radar.identities (
  identity_id text primary key,
  display_name text not null,
  aliases text[] not null default '{}',
  lifecycle_status text not null default 'ACTIVE'
    check (lifecycle_status in ('ACTIVE','PAUSED','ARCHIVED')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists radar.nodes (
  node_id uuid primary key default gen_random_uuid(),
  identity_id text not null references radar.identities(identity_id) on delete restrict,
  runtime_kind text not null,
  status text not null default 'OFFLINE'
    check (status in ('ONLINE','IDLE','WORKING','WAITING_DEPENDENCY','DEGRADED','OFFLINE','PAUSED','ARCHIVED')),
  capabilities text[] not null default '{}',
  last_heartbeat timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists radar_nodes_identity_idx on radar.nodes(identity_id);
create index if not exists radar_nodes_status_heartbeat_idx on radar.nodes(status, last_heartbeat);

create table if not exists radar.endpoints (
  endpoint_id text primary key,
  identity_id text not null references radar.identities(identity_id) on delete restrict,
  node_id uuid references radar.nodes(node_id) on delete set null,
  transport text not null,
  address text not null,
  status text not null default 'ACTIVE' check (status in ('ACTIVE','DEGRADED','OFFLINE','ARCHIVED')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (transport, address)
);
create index if not exists radar_endpoints_identity_idx on radar.endpoints(identity_id);

create table if not exists radar.subscriptions (
  subscription_id uuid primary key default gen_random_uuid(),
  identity_id text not null references radar.identities(identity_id) on delete cascade,
  domain text not null,
  intent text,
  min_priority integer not null default 3 check (min_priority between 0 and 4),
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create unique index if not exists radar_subscriptions_unique_idx
  on radar.subscriptions(identity_id, domain, coalesce(intent, ''), min_priority);

create table if not exists radar.messages (
  message_id text primary key,
  schema_version integer not null check (schema_version > 0),
  created_at timestamptz not null,
  sender text not null,
  audience text[] not null default '{}',
  domain text not null,
  intent text not null,
  priority integer not null check (priority between 0 and 4),
  root_task_id text,
  correlation_id text,
  causal_parent_id text references radar.messages(message_id) on delete set null,
  requires_ack boolean not null default false,
  expires_at timestamptz,
  authority_ref text,
  source_refs text[] not null default '{}',
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  idempotency_key text not null,
  payload jsonb not null,
  projection_status text not null default 'ACCEPTED'
    check (projection_status in ('ACCEPTED','DELIVERING','DELIVERED','DLQ','EXPIRED')),
  inserted_at timestamptz not null default now()
);
create index if not exists radar_messages_domain_priority_idx on radar.messages(domain, priority, created_at);
create index if not exists radar_messages_root_task_idx on radar.messages(root_task_id) where root_task_id is not null;
create index if not exists radar_messages_correlation_idx on radar.messages(correlation_id) where correlation_id is not null;
create index if not exists radar_messages_idempotency_idx on radar.messages(idempotency_key);

create table if not exists radar.delivery_events (
  event_id bigint generated always as identity primary key,
  message_id text not null references radar.messages(message_id) on delete restrict,
  identity_id text references radar.identities(identity_id) on delete restrict,
  endpoint_id text references radar.endpoints(endpoint_id) on delete restrict,
  event_type text not null,
  outcome text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists radar_delivery_message_idx on radar.delivery_events(message_id, event_id);

create table if not exists radar.acknowledgements (
  acknowledgement_id uuid primary key default gen_random_uuid(),
  message_id text not null references radar.messages(message_id) on delete restrict,
  identity_id text not null references radar.identities(identity_id) on delete restrict,
  acknowledgement_type text not null
    check (acknowledgement_type in ('DELIVERED','READ','INCORPORATED','REJECTED')),
  evidence_ref text,
  created_at timestamptz not null default now(),
  unique (message_id, identity_id, acknowledgement_type)
);

create table if not exists radar.dead_letters (
  item_id bigint generated always as identity primary key,
  message_id text,
  failure_stage text not null,
  failure_code text not null,
  envelope jsonb not null,
  diagnostic text not null,
  route_attempted jsonb,
  applicability text not null default 'UNKNOWN'
    check (applicability in ('UNKNOWN','APPLICABLE','NOT_APPLICABLE')),
  replay_state text not null default 'PENDING'
    check (replay_state in ('PENDING','REPLAYED','DISCARDED')),
  retry_count integer not null default 0 check (retry_count >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists radar_dead_letters_pending_idx
  on radar.dead_letters(replay_state, applicability, created_at);

create table if not exists radar.assignments (
  assignment_id text primary key,
  workflow_id text not null,
  current_state text not null default 'DRAFTED'
    check (current_state in ('DRAFTED','ASSIGNED','ACKNOWLEDGED','RUNNING','REVIEW_PENDING','PAUSED_USAGE_EXHAUSTED','INTERRUPTED','HANDED_OFF','CHANGES_REQUESTED','STOPPED','CONFIRMED','SUPERSEDED')),
  authority_ref text,
  protected_authority boolean not null default false,
  current_event_id bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists radar.assignment_events (
  event_id bigint generated always as identity primary key,
  assignment_id text not null references radar.assignments(assignment_id) on delete restrict,
  event_type text not null,
  actor text not null,
  from_state text not null,
  to_state text not null,
  predecessor_event_id bigint references radar.assignment_events(event_id) on delete restrict,
  operation_id text not null,
  receipt_sha256 text not null check (receipt_sha256 ~ '^[0-9a-f]{64}$'),
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (assignment_id, operation_id)
);
alter table radar.assignments
  drop constraint if exists radar_assignments_current_event_fk;
alter table radar.assignments
  add constraint radar_assignments_current_event_fk
  foreign key (current_event_id) references radar.assignment_events(event_id) on delete restrict;
create index if not exists radar_assignment_events_assignment_idx on radar.assignment_events(assignment_id, event_id);

create table if not exists radar.dependencies (
  predecessor_assignment_id text not null references radar.assignments(assignment_id) on delete cascade,
  successor_assignment_id text not null references radar.assignments(assignment_id) on delete cascade,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (predecessor_assignment_id, successor_assignment_id),
  check (predecessor_assignment_id <> successor_assignment_id)
);
create index if not exists radar_dependencies_successor_idx on radar.dependencies(successor_assignment_id) where active;

create table if not exists radar.health_events (
  event_id bigint generated always as identity primary key,
  identity_id text references radar.identities(identity_id) on delete restrict,
  node_id uuid references radar.nodes(node_id) on delete restrict,
  event_type text not null,
  status text,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists radar.reconciliation_events (
  event_id bigint generated always as identity primary key,
  code text not null,
  repair_class text not null check (repair_class in ('SAFE_PROJECTION_REPAIR','AMBIGUOUS_DURABLE_CONFLICT','OBSERVATION_ONLY')),
  source_ref text,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists radar.telemetry (
  event_id bigint generated always as identity primary key,
  metric text not null,
  value double precision not null,
  labels jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists radar_telemetry_metric_time_idx on radar.telemetry(metric, created_at desc);

create or replace function radar.assignment_ready(p_assignment_id text)
returns boolean
language sql
stable
set search_path = pg_catalog, radar
as $$
  select not exists (
    select 1
    from radar.dependencies d
    join radar.assignments predecessor
      on predecessor.assignment_id = d.predecessor_assignment_id
    where d.successor_assignment_id = p_assignment_id
      and d.active
      and predecessor.current_state not in ('CONFIRMED','HANDED_OFF','SUPERSEDED')
  );
$$;

create or replace function radar.mark_stale_nodes_offline(p_stale_after interval default interval '5 minutes')
returns integer
language plpgsql
set search_path = pg_catalog, radar
as $$
declare
  changed integer;
begin
  update radar.nodes
  set status = 'OFFLINE', updated_at = now()
  where status in ('ONLINE','IDLE','WORKING','WAITING_DEPENDENCY','DEGRADED')
    and last_heartbeat is not null
    and last_heartbeat < now() - p_stale_after;
  get diagnostics changed = row_count;
  return changed;
end;
$$;

create or replace function radar.touch_node(
  p_node_id uuid,
  p_status text default 'ONLINE'
)
returns radar.nodes
language plpgsql
set search_path = pg_catalog, radar
as $$
declare
  result radar.nodes;
begin
  if p_status not in ('ONLINE','IDLE','WORKING','WAITING_DEPENDENCY','DEGRADED','OFFLINE','PAUSED','ARCHIVED') then
    raise exception 'RADAR_INVALID_NODE_STATUS';
  end if;
  update radar.nodes
  set status = p_status, last_heartbeat = now(), updated_at = now()
  where node_id = p_node_id
  returning * into result;
  if result.node_id is null then
    raise exception 'RADAR_UNKNOWN_NODE';
  end if;
  return result;
end;
$$;

create or replace function radar.prevent_dependency_cycle()
returns trigger
language plpgsql
set search_path = pg_catalog, radar
as $$
begin
  if exists (
    with recursive reachable(id) as (
      select new.successor_assignment_id
      union
      select d.successor_assignment_id
      from radar.dependencies d
      join reachable r on d.predecessor_assignment_id = r.id
      where d.active
    )
    select 1 from reachable where id = new.predecessor_assignment_id
  ) then
    raise exception 'RADAR_DEPENDENCY_CYCLE' using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists radar_dependencies_no_cycle on radar.dependencies;
create trigger radar_dependencies_no_cycle
before insert or update on radar.dependencies
for each row execute function radar.prevent_dependency_cycle();

-- Event/audit tables are append-only even for privileged callers.
do $$
declare
  table_name text;
begin
  foreach table_name in array array['delivery_events','assignment_events','health_events','reconciliation_events','telemetry']
  loop
    execute format('drop trigger if exists radar_append_only_%I on radar.%I', table_name, table_name);
    execute format(
      'create trigger radar_append_only_%I before update or delete on radar.%I for each row execute function radar.prevent_event_mutation()',
      table_name,
      table_name
    );
  end loop;
end
$$;

-- Defense in depth. The radar schema is not intended for anon/authenticated API use.
do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'identities','nodes','endpoints','subscriptions','messages','delivery_events',
    'acknowledgements','dead_letters','assignments','assignment_events','dependencies',
    'health_events','reconciliation_events','telemetry'
  ]
  loop
    execute format('alter table radar.%I enable row level security', table_name);
    execute format('revoke all on table radar.%I from public, anon, authenticated', table_name);
    execute format('grant select, insert, update, delete on table radar.%I to service_role', table_name);
  end loop;
end
$$;

grant usage, select on all sequences in schema radar to service_role;
grant execute on function radar.assignment_ready(text) to service_role;
grant execute on function radar.mark_stale_nodes_offline(interval) to service_role;
grant execute on function radar.touch_node(uuid, text) to service_role;
revoke all on function radar.prevent_event_mutation() from public, anon, authenticated;
revoke all on function radar.prevent_dependency_cycle() from public, anon, authenticated;
