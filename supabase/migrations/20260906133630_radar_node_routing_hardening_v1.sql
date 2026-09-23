-- Radar node-level routing and lease hardening v1.
-- Forward-only: preserves historical migrations and fails closed where legacy
-- identity-level subscriptions cannot be mapped to exactly one node.

alter table radar.nodes
  add column if not exists lease_expires_at timestamptz;

create index if not exists radar_nodes_status_lease_idx
  on radar.nodes(status, lease_expires_at);

alter table radar.subscriptions
  add column if not exists node_id uuid references radar.nodes(node_id) on delete cascade;

-- Legacy subscriptions were identity-bound. Bind only when the identity has
-- exactly one registered node; anything ambiguous remains unbound.
with unique_identity_nodes as (
  select
    identity_id,
    (array_agg(node_id order by node_id::text))[1] as node_id
  from radar.nodes
  group by identity_id
  having count(*) = 1
)
update radar.subscriptions s
set node_id = u.node_id
from unique_identity_nodes u
where s.node_id is null
  and s.identity_id = u.identity_id;

-- Never infer authority for an ambiguous or node-less legacy subscription.
update radar.subscriptions
set active = false
where active
  and node_id is null;

-- Existing nodes predate explicit leases. Routable status without a lease is
-- not current routing authority, so fail closed until the next heartbeat.
update radar.nodes
set status = 'OFFLINE', updated_at = now()
where status in ('ONLINE','IDLE','WORKING','WAITING_DEPENDENCY','DEGRADED')
  and (last_heartbeat is null or lease_expires_at is null);

alter table radar.subscriptions
  drop constraint if exists radar_subscriptions_active_node_bound;
alter table radar.subscriptions
  add constraint radar_subscriptions_active_node_bound
  check (not active or node_id is not null);

drop index if exists radar.radar_subscriptions_unique_idx;
create unique index if not exists radar_subscriptions_node_unique_idx
  on radar.subscriptions(node_id, domain, coalesce(intent, ''), min_priority)
  where node_id is not null;

create index if not exists radar_subscriptions_identity_idx
  on radar.subscriptions(identity_id);
create index if not exists radar_subscriptions_node_active_idx
  on radar.subscriptions(node_id, active, domain, intent, min_priority)
  where node_id is not null;

create or replace function radar.validate_subscription_node_identity()
returns trigger
language plpgsql
set search_path = pg_catalog, radar
as $$
declare
  node_identity text;
begin
  if new.node_id is null then
    if new.active then
      raise exception 'RADAR_ACTIVE_SUBSCRIPTION_NODE_REQUIRED' using errcode = '23514';
    end if;
    return new;
  end if;

  select n.identity_id
  into node_identity
  from radar.nodes n
  where n.node_id = new.node_id;

  if node_identity is null then
    raise exception 'RADAR_SUBSCRIPTION_NODE_UNKNOWN' using errcode = '23503';
  end if;

  if node_identity <> new.identity_id then
    raise exception 'RADAR_SUBSCRIPTION_NODE_IDENTITY_MISMATCH' using errcode = '23514';
  end if;

  return new;
end;
$$;

drop trigger if exists radar_subscriptions_node_identity_guard on radar.subscriptions;
create trigger radar_subscriptions_node_identity_guard
before insert or update of node_id, identity_id, active on radar.subscriptions
for each row execute function radar.validate_subscription_node_identity();

-- Node ownership is provenance and authority. Reassigning a node after
-- subscriptions exist would silently transfer or invalidate routing authority,
-- so identity ownership is immutable after insert.
create or replace function radar.prevent_node_identity_reassignment()
returns trigger
language plpgsql
set search_path = pg_catalog, radar
as $$
begin
  if new.identity_id is distinct from old.identity_id then
    raise exception 'RADAR_NODE_IDENTITY_IMMUTABLE' using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists radar_nodes_identity_immutable on radar.nodes;
create trigger radar_nodes_identity_immutable
before update of identity_id on radar.nodes
for each row execute function radar.prevent_node_identity_reassignment();

create or replace function radar.mark_stale_nodes_offline(
  p_stale_after interval default interval '5 minutes'
)
returns integer
language plpgsql
set search_path = pg_catalog, radar
as $$
declare
  changed integer;
begin
  if p_stale_after is null or p_stale_after <= interval '0 seconds' then
    raise exception 'RADAR_INVALID_STALE_INTERVAL' using errcode = '22023';
  end if;

  update radar.nodes
  set status = 'OFFLINE', updated_at = now()
  where status in ('ONLINE','IDLE','WORKING','WAITING_DEPENDENCY','DEGRADED')
    and (
      last_heartbeat is null
      or lease_expires_at is null
      or lease_expires_at <= now()
      or last_heartbeat < now() - p_stale_after
    );
  get diagnostics changed = row_count;
  return changed;
end;
$$;

-- Preserve the existing function signature for callers while upgrading its
-- semantics: every successful heartbeat renews a five-minute routing lease.
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
    raise exception 'RADAR_INVALID_NODE_STATUS' using errcode = '22023';
  end if;

  update radar.nodes
  set
    status = p_status,
    last_heartbeat = now(),
    lease_expires_at = case
      when p_status in ('ONLINE','IDLE','WORKING','WAITING_DEPENDENCY','DEGRADED')
        then now() + interval '5 minutes'
      else null
    end,
    updated_at = now()
  where node_id = p_node_id
  returning * into result;

  if result.node_id is null then
    raise exception 'RADAR_UNKNOWN_NODE' using errcode = 'P0002';
  end if;
  return result;
end;
$$;

-- Close the historical default-PUBLIC EXECUTE gap and keep Radar functions
-- service-role/internal only.
revoke all on function radar.assignment_ready(text) from public, anon, authenticated;
revoke all on function radar.mark_stale_nodes_offline(interval) from public, anon, authenticated;
revoke all on function radar.touch_node(uuid, text) from public, anon, authenticated;
revoke all on function radar.validate_subscription_node_identity() from public, anon, authenticated;
revoke all on function radar.prevent_node_identity_reassignment() from public, anon, authenticated;

grant execute on function radar.assignment_ready(text) to service_role;
grant execute on function radar.mark_stale_nodes_offline(interval) to service_role;
grant execute on function radar.touch_node(uuid, text) to service_role;
