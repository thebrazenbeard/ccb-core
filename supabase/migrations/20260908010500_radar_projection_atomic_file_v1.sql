-- Radar projection atomic-file hardening v1.
-- Source-only until separately authorized provider migration.
--
-- A successful file projection and its journal terminal state are now one SQL
-- transaction. Provider projection cannot occur unless the claim, lane cursor,
-- control/topology cut, and claimed path are all current. Journal lock order is
-- normalized to lane -> batch -> file to avoid claim/finalize inversion.

create or replace function radar.projection_project_file_v1(
  p_batch_id uuid,
  p_claim_token uuid,
  p_path text,
  p_message_id text,
  p_created_at timestamptz,
  p_sender text,
  p_audience text[],
  p_requires_ack boolean,
  p_source_refs text[],
  p_content_hash text,
  p_idempotency_key text,
  p_payload jsonb
)
returns text
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  lane_key_identity text;
  lane_key_branch text;
  lane radar.projection_lane_state;
  batch radar.projection_batches;
  current_file radar.projection_batch_files;
  projection_result text;
  terminal_state text;
begin
  if p_batch_id is null
     or p_claim_token is null
     or p_path is null
     or btrim(p_path) = '' then
    raise exception 'RADAR_PROJECTION_FILE_REQUEST_INVALID' using errcode = '22023';
  end if;

  -- This first read discovers the lane key only. It grants no authority. The
  -- batch is re-read and fully revalidated after the lane lock is held.
  select identity_id, branch
    into lane_key_identity, lane_key_branch
  from radar.projection_batches
  where batch_id = p_batch_id;

  if lane_key_identity is null then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
  end if;

  select * into lane
  from radar.projection_lane_state
  where identity_id = lane_key_identity
    and branch = lane_key_branch
  for update;

  if lane.identity_id is null then
    raise exception 'RADAR_PROJECTION_LANE_UNKNOWN' using errcode = 'P0002';
  end if;

  select * into batch
  from radar.projection_batches
  where batch_id = p_batch_id
  for update;

  if batch.batch_id is null
     or batch.identity_id <> lane_key_identity
     or batch.branch <> lane_key_branch
     or batch.claim_token <> p_claim_token
     or batch.status <> 'CLAIMED'
     or batch.claim_expires_at <= now() then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
  end if;

  if lane.projected_head_sha is distinct from batch.expected_projected_head_sha then
    raise exception 'RADAR_PROJECTION_BASE_CHANGED' using errcode = '40001';
  end if;
  if lane.control_sha is distinct from batch.control_sha
     or lane.topology_sha is distinct from batch.topology_sha then
    raise exception 'RADAR_PROJECTION_CONTROL_CUT_CHANGED' using errcode = '40001';
  end if;

  select * into current_file
  from radar.projection_batch_files
  where batch_id = p_batch_id and path = p_path
  for update;

  if current_file.path is null then
    raise exception 'RADAR_PROJECTION_FILE_UNKNOWN' using errcode = 'P0002';
  end if;

  if current_file.state in ('PROJECTED', 'IDEMPOTENT') then
    if current_file.result_ref is null
       or not (
         current_file.result_ref in ('INSERTED', 'IDEMPOTENT')
         or current_file.result_ref ~ '^CONFLICT_INSERTED:.+$'
         or current_file.result_ref ~ '^CONFLICT_IDEMPOTENT:.+$'
       ) then
      raise exception 'RADAR_PROJECTION_FILE_RESULT_CORRUPT' using errcode = 'XX000';
    end if;
    return current_file.result_ref;
  end if;

  if current_file.state <> 'PENDING' then
    raise exception 'RADAR_PROJECTION_FILE_NOT_PROJECTABLE' using errcode = '23514';
  end if;

  -- Bind the provider message mutation to the exact claimed lane/path/target.
  if p_sender is null or lower(btrim(p_sender)) <> batch.identity_id then
    raise exception 'RADAR_PROJECTION_SENDER_MISMATCH' using errcode = '23514';
  end if;
  if p_payload is null
     or jsonb_typeof(p_payload) <> 'object'
     or p_payload->>'admitted_lane_identity' is distinct from batch.identity_id
     or p_payload->>'source_path' is distinct from p_path
     or p_payload->>'source_branch_head' is distinct from batch.target_head_sha then
    raise exception 'RADAR_PROJECTION_SOURCE_BINDING_MISMATCH' using errcode = '23514';
  end if;

  projection_result := radar.project_bus_message_v1(
    p_message_id,
    p_created_at,
    p_sender,
    p_audience,
    p_requires_ack,
    p_source_refs,
    p_content_hash,
    p_idempotency_key,
    p_payload
  );

  if projection_result = 'INSERTED'
     or projection_result ~ '^CONFLICT_INSERTED:.+$' then
    terminal_state := 'PROJECTED';
  elsif projection_result = 'IDEMPOTENT'
     or projection_result ~ '^CONFLICT_IDEMPOTENT:.+$' then
    terminal_state := 'IDEMPOTENT';
  else
    -- Because this function is one transaction, an unknown result rolls back
    -- any message/reconciliation mutation performed above.
    raise exception 'RADAR_PROJECTION_RESULT_INVALID' using errcode = 'XX000';
  end if;

  update radar.projection_batch_files
  set state = terminal_state,
      result_ref = projection_result,
      updated_at = now()
  where batch_id = p_batch_id
    and path = p_path
    and state = 'PENDING';

  if not found then
    raise exception 'RADAR_PROJECTION_FILE_STATE_RACE' using errcode = '40001';
  end if;

  return projection_result;
end;
$$;

-- Replace finalize with the same lane -> batch lock order used by claim and
-- atomic file projection. The initial batch read discovers only the lane key;
-- all authority-sensitive state is re-read after locks are acquired.
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
  lane_key_identity text;
  lane_key_branch text;
  batch radar.projection_batches;
  lane radar.projection_lane_state;
  result radar.projection_lane_state;
begin
  select identity_id, branch
    into lane_key_identity, lane_key_branch
  from radar.projection_batches
  where batch_id = p_batch_id;

  if lane_key_identity is null then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
  end if;

  select * into lane
  from radar.projection_lane_state
  where identity_id = lane_key_identity
    and branch = lane_key_branch
  for update;

  if lane.identity_id is null then
    raise exception 'RADAR_PROJECTION_LANE_UNKNOWN' using errcode = 'P0002';
  end if;

  select * into batch
  from radar.projection_batches
  where batch_id = p_batch_id
  for update;

  if batch.batch_id is null
     or batch.identity_id <> lane_key_identity
     or batch.branch <> lane_key_branch
     or batch.claim_token <> p_claim_token then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
  end if;

  if batch.status = 'COMPLETE' then
    if lane.projected_head_sha is distinct from batch.target_head_sha then
      raise exception 'RADAR_PROJECTION_COMPLETE_CURSOR_MISMATCH' using errcode = 'XX000';
    end if;
    return lane;
  end if;

  if batch.status <> 'CLAIMED' or batch.claim_expires_at <= now() then
    raise exception 'RADAR_PROJECTION_STALE_CLAIM' using errcode = '40001';
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

-- PostgREST bridge. It is the only service-role path that may turn a claimed
-- file into a successful provider projection/journal result.
create or replace function public.radar_projection_project_file_v1(
  p_batch_id uuid,
  p_claim_token uuid,
  p_path text,
  p_message_id text,
  p_created_at timestamptz,
  p_sender text,
  p_audience text[],
  p_requires_ack boolean,
  p_source_refs text[],
  p_content_hash text,
  p_idempotency_key text,
  p_payload jsonb
)
returns text
language sql
security definer
set search_path = pg_catalog, radar
as $$
  select radar.projection_project_file_v1(
    p_batch_id,
    p_claim_token,
    p_path,
    p_message_id,
    p_created_at,
    p_sender,
    p_audience,
    p_requires_ack,
    p_source_refs,
    p_content_hash,
    p_idempotency_key,
    p_payload
  );
$$;

revoke all on function radar.projection_project_file_v1(uuid,uuid,text,text,timestamptz,text,text[],boolean,text[],text,text,jsonb)
  from public, anon, authenticated, service_role;
revoke all on function public.radar_projection_project_file_v1(uuid,uuid,text,text,timestamptz,text,text[],boolean,text[],text,text,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.radar_projection_project_file_v1(uuid,uuid,text,text,timestamptz,text,text[],boolean,text[],text,text,jsonb)
  to service_role;

-- Remove the two service-role bypasses that could independently mutate a
-- message or independently mark a journal file successful.
revoke execute on function radar.project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb)
  from service_role;
revoke execute on function public.radar_project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb)
  from service_role;
revoke execute on function radar.projection_record_file_result_v1(uuid,uuid,text,text,text)
  from service_role;
revoke execute on function public.radar_projection_record_file_result_v1(uuid,uuid,text,text,text)
  from service_role;
