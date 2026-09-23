-- Radar durable projection journal v1.
-- Source-only until separately authorized provider migration.

create table if not exists radar.projection_lane_state (
  identity_id text not null,
  branch text not null,
  projected_head_sha text,
  control_sha text,
  topology_sha text,
  updated_at timestamptz not null default now(),
  primary key (identity_id, branch),
  check (length(identity_id) > 0),
  check (length(branch) > 0),
  check (projected_head_sha is null or projected_head_sha ~ '^[0-9a-f]{40}$'),
  check (control_sha is null or control_sha ~ '^[0-9a-f]{40}$'),
  check (topology_sha is null or topology_sha ~ '^[0-9a-f]{40}$')
);

create table if not exists radar.projection_batches (
  batch_id uuid primary key default gen_random_uuid(),
  identity_id text not null,
  branch text not null,
  expected_projected_head_sha text,
  target_head_sha text not null,
  control_sha text not null,
  topology_sha text not null,
  claim_token uuid not null default gen_random_uuid(),
  claim_owner text not null,
  claim_expires_at timestamptz not null,
  status text not null default 'CLAIMED'
    check (status in ('CLAIMED','COMPLETE','STALE','FAILED')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  foreign key (identity_id, branch)
    references radar.projection_lane_state(identity_id, branch)
    on delete restrict,
  check (expected_projected_head_sha is null or expected_projected_head_sha ~ '^[0-9a-f]{40}$'),
  check (target_head_sha ~ '^[0-9a-f]{40}$'),
  check (control_sha ~ '^[0-9a-f]{40}$'),
  check (topology_sha ~ '^[0-9a-f]{40}$'),
  check (length(claim_owner) > 0)
);

create unique index if not exists radar_projection_one_claimed_batch_per_lane
  on radar.projection_batches(identity_id, branch)
  where status = 'CLAIMED';

create index if not exists radar_projection_batches_lane_created_idx
  on radar.projection_batches(identity_id, branch, created_at desc);

create table if not exists radar.projection_batch_files (
  batch_id uuid not null references radar.projection_batches(batch_id) on delete cascade,
  path text not null,
  state text not null default 'PENDING'
    check (state in ('PENDING','PROJECTED','IDEMPOTENT','FAILED','CONFLICT')),
  result_ref text,
  updated_at timestamptz not null default now(),
  primary key (batch_id, path),
  check (length(path) > 0)
);

create index if not exists radar_projection_batch_files_state_idx
  on radar.projection_batch_files(batch_id, state);

alter table radar.projection_lane_state enable row level security;
alter table radar.projection_batches enable row level security;
alter table radar.projection_batch_files enable row level security;

revoke all on table radar.projection_lane_state from public, anon, authenticated, service_role;
revoke all on table radar.projection_batches from public, anon, authenticated, service_role;
revoke all on table radar.projection_batch_files from public, anon, authenticated, service_role;
grant select on table radar.projection_lane_state to service_role;
grant select on table radar.projection_batches to service_role;
grant select on table radar.projection_batch_files to service_role;

create or replace function radar.projection_seed_lane_v1(
  p_identity_id text,
  p_branch text,
  p_projected_head_sha text
)
returns radar.projection_lane_state
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  result radar.projection_lane_state;
begin
  if p_identity_id is null or btrim(p_identity_id) = ''
     or p_branch is null or btrim(p_branch) = '' then
    raise exception 'RADAR_PROJECTION_LANE_INVALID' using errcode = '22023';
  end if;
  if p_projected_head_sha is not null
     and p_projected_head_sha !~ '^[0-9a-f]{40}$' then
    raise exception 'RADAR_PROJECTION_HEAD_INVALID' using errcode = '22023';
  end if;

  insert into radar.projection_lane_state(identity_id, branch, projected_head_sha)
  values (p_identity_id, p_branch, p_projected_head_sha)
  on conflict (identity_id, branch) do nothing;

  select * into result
  from radar.projection_lane_state
  where identity_id = p_identity_id and branch = p_branch;

  if result.projected_head_sha is distinct from p_projected_head_sha then
    raise exception 'RADAR_PROJECTION_LANE_ALREADY_SEEDED' using errcode = '23514';
  end if;
  return result;
end;
$$;

create or replace function radar.projection_set_control_cut_v1(
  p_identity_id text,
  p_branch text,
  p_control_sha text,
  p_topology_sha text
)
returns radar.projection_lane_state
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  result radar.projection_lane_state;
begin
  if p_control_sha is null or p_control_sha !~ '^[0-9a-f]{40}$'
     or p_topology_sha is null or p_topology_sha !~ '^[0-9a-f]{40}$' then
    raise exception 'RADAR_PROJECTION_CONTROL_CUT_INVALID' using errcode = '22023';
  end if;

  update radar.projection_lane_state
  set control_sha = p_control_sha,
      topology_sha = p_topology_sha,
      updated_at = now()
  where identity_id = p_identity_id and branch = p_branch
  returning * into result;

  if result.identity_id is null then
    raise exception 'RADAR_PROJECTION_LANE_UNKNOWN' using errcode = 'P0002';
  end if;
  return result;
end;
$$;

create or replace function radar.projection_claim_batch_v1(
  p_identity_id text,
  p_branch text,
  p_expected_projected_head_sha text,
  p_target_head_sha text,
  p_control_sha text,
  p_topology_sha text,
  p_files text[],
  p_claim_owner text,
  p_lease_seconds integer default 300
)
returns radar.projection_batches
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  lane radar.projection_lane_state;
  active_batch radar.projection_batches;
  result radar.projection_batches;
  file_path text;
begin
  if p_target_head_sha is null or p_target_head_sha !~ '^[0-9a-f]{40}$'
     or (p_expected_projected_head_sha is not null
         and p_expected_projected_head_sha !~ '^[0-9a-f]{40}$') then
    raise exception 'RADAR_PROJECTION_HEAD_INVALID' using errcode = '22023';
  end if;
  if p_claim_owner is null or btrim(p_claim_owner) = ''
     or p_control_sha is null or p_control_sha !~ '^[0-9a-f]{40}$'
     or p_topology_sha is null or p_topology_sha !~ '^[0-9a-f]{40}$'
     or p_lease_seconds is null or p_lease_seconds <= 0 then
    raise exception 'RADAR_PROJECTION_CLAIM_INVALID' using errcode = '22023';
  end if;
  if p_files is null then
    p_files := array[]::text[];
  end if;
  if exists (
    select 1 from unnest(p_files) f
    where f is null or btrim(f) = ''
  ) or cardinality(p_files) <> (select count(distinct f) from unnest(p_files) f) then
    raise exception 'RADAR_PROJECTION_FILES_INVALID' using errcode = '22023';
  end if;

  select * into lane
  from radar.projection_lane_state
  where identity_id = p_identity_id and branch = p_branch
  for update;

  if lane.identity_id is null then
    raise exception 'RADAR_PROJECTION_LANE_UNSEEDED' using errcode = '23514';
  end if;
  if lane.projected_head_sha is distinct from p_expected_projected_head_sha then
    raise exception 'RADAR_PROJECTION_BASE_CHANGED' using errcode = '40001';
  end if;
  if lane.control_sha is distinct from p_control_sha
     or lane.topology_sha is distinct from p_topology_sha then
    raise exception 'RADAR_PROJECTION_CONTROL_CUT_CHANGED' using errcode = '40001';
  end if;

  select * into active_batch
  from radar.projection_batches
  where identity_id = p_identity_id
    and branch = p_branch
    and status = 'CLAIMED'
  for update;

  if active_batch.batch_id is not null then
    if active_batch.claim_expires_at > now() then
      raise exception 'RADAR_PROJECTION_CLAIM_BUSY' using errcode = '55P03';
    end if;
    update radar.projection_batches
    set status = 'STALE', updated_at = now()
    where batch_id = active_batch.batch_id;
  end if;

  insert into radar.projection_batches(
    identity_id, branch, expected_projected_head_sha, target_head_sha,
    control_sha, topology_sha, claim_owner, claim_expires_at
  ) values (
    p_identity_id, p_branch, p_expected_projected_head_sha, p_target_head_sha,
    p_control_sha, p_topology_sha, p_claim_owner,
    now() + make_interval(secs => p_lease_seconds)
  ) returning * into result;

  foreach file_path in array p_files loop
    insert into radar.projection_batch_files(batch_id, path)
    values (result.batch_id, file_path);
  end loop;
  return result;
end;
$$;

create or replace function radar.projection_record_file_result_v1(
  p_batch_id uuid,
  p_claim_token uuid,
  p_path text,
  p_state text,
  p_result_ref text default null
)
returns radar.projection_batch_files
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  batch radar.projection_batches;
  current_file radar.projection_batch_files;
  result radar.projection_batch_files;
begin
  if p_state not in ('PROJECTED','IDEMPOTENT','FAILED','CONFLICT') then
    raise exception 'RADAR_PROJECTION_FILE_STATE_INVALID' using errcode = '22023';
  end if;

  select * into batch
  from radar.projection_batches
  where batch_id = p_batch_id
  for update;

  if batch.batch_id is null
     or batch.claim_token <> p_claim_token
     or batch.status <> 'CLAIMED'
     or batch.claim_expires_at <= now() then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
  end if;

  select * into current_file
  from radar.projection_batch_files
  where batch_id = p_batch_id and path = p_path
  for update;

  if current_file.path is null then
    raise exception 'RADAR_PROJECTION_FILE_UNKNOWN' using errcode = 'P0002';
  end if;
  if current_file.state <> 'PENDING' and current_file.state <> p_state then
    raise exception 'RADAR_PROJECTION_FILE_RESULT_CONFLICT' using errcode = '23514';
  end if;

  update radar.projection_batch_files
  set state = p_state, result_ref = p_result_ref, updated_at = now()
  where batch_id = p_batch_id and path = p_path
  returning * into result;
  return result;
end;
$$;

create or replace function radar.projection_finalize_batch_v1(
  p_batch_id uuid,
  p_claim_token uuid
)
returns radar.projection_lane_state
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  batch radar.projection_batches;
  lane radar.projection_lane_state;
  result radar.projection_lane_state;
begin
  select * into batch
  from radar.projection_batches
  where batch_id = p_batch_id
  for update;

  if batch.batch_id is null or batch.claim_token <> p_claim_token then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
  end if;

  if batch.status = 'COMPLETE' then
    select * into result
    from radar.projection_lane_state
    where identity_id = batch.identity_id and branch = batch.branch;
    if result.projected_head_sha is distinct from batch.target_head_sha then
      raise exception 'RADAR_PROJECTION_COMPLETE_CURSOR_MISMATCH' using errcode = 'XX000';
    end if;
    return result;
  end if;

  if batch.status <> 'CLAIMED' or batch.claim_expires_at <= now() then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
  end if;

  select * into lane
  from radar.projection_lane_state
  where identity_id = batch.identity_id and branch = batch.branch
  for update;

  if lane.identity_id is null then
    raise exception 'RADAR_PROJECTION_LANE_UNKNOWN' using errcode = 'P0002';
  end if;
  if lane.projected_head_sha is distinct from batch.expected_projected_head_sha then
    raise exception 'RADAR_PROJECTION_BASE_CHANGED' using errcode = '40001';
  end if;
  if lane.control_sha is distinct from batch.control_sha
     or lane.topology_sha is distinct from batch.topology_sha then
    raise exception 'RADAR_PROJECTION_CONTROL_CUT_CHANGED' using errcode = '40001';
  end if;
  if exists (
    select 1 from radar.projection_batch_files
    where batch_id = batch.batch_id
      and state not in ('PROJECTED','IDEMPOTENT')
  ) then
    raise exception 'RADAR_PROJECTION_UNRESOLVED_FILES' using errcode = '23514';
  end if;

  update radar.projection_lane_state
  set projected_head_sha = batch.target_head_sha,
      updated_at = now()
  where identity_id = batch.identity_id and branch = batch.branch
  returning * into result;

  update radar.projection_batches
  set status = 'COMPLETE', completed_at = now(), updated_at = now()
  where batch_id = batch.batch_id;

  return result;
end;
$$;

revoke all on function radar.projection_seed_lane_v1(text,text,text) from public, anon, authenticated;
revoke all on function radar.projection_set_control_cut_v1(text,text,text,text) from public, anon, authenticated;
revoke all on function radar.projection_claim_batch_v1(text,text,text,text,text,text,text[],text,integer) from public, anon, authenticated;
revoke all on function radar.projection_record_file_result_v1(uuid,uuid,text,text,text) from public, anon, authenticated;
revoke all on function radar.projection_finalize_batch_v1(uuid,uuid) from public, anon, authenticated;

grant execute on function radar.projection_seed_lane_v1(text,text,text) to service_role;
grant execute on function radar.projection_set_control_cut_v1(text,text,text,text) to service_role;
grant execute on function radar.projection_claim_batch_v1(text,text,text,text,text,text,text[],text,integer) to service_role;
grant execute on function radar.projection_record_file_result_v1(uuid,uuid,text,text,text) to service_role;
grant execute on function radar.projection_finalize_batch_v1(uuid,uuid) to service_role;
