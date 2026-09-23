-- Radar projection-journal PostgREST bridge v1.
-- Source-only until separately authorized provider migration.
-- Lane seeding is intentionally NOT exposed here; bootstrap remains a protected cutover effect.

create or replace function public.radar_projection_get_lane_v1(
  p_identity_id text,
  p_branch text
)
returns jsonb
language sql
security definer
set search_path = pg_catalog, radar
as $$
  select to_jsonb(s)
  from radar.projection_lane_state s
  where s.identity_id = p_identity_id
    and s.branch = p_branch;
$$;

create or replace function public.radar_projection_set_control_cut_v1(
  p_identity_id text,
  p_branch text,
  p_control_sha text,
  p_topology_sha text
)
returns jsonb
language sql
security definer
set search_path = pg_catalog, radar
as $$
  select to_jsonb(
    radar.projection_set_control_cut_v1(
      p_identity_id,
      p_branch,
      p_control_sha,
      p_topology_sha
    )
  );
$$;

create or replace function public.radar_projection_claim_batch_v1(
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
returns jsonb
language sql
security definer
set search_path = pg_catalog, radar
as $$
  select to_jsonb(
    radar.projection_claim_batch_v1(
      p_identity_id,
      p_branch,
      p_expected_projected_head_sha,
      p_target_head_sha,
      p_control_sha,
      p_topology_sha,
      p_files,
      p_claim_owner,
      p_lease_seconds
    )
  );
$$;

create or replace function public.radar_projection_record_file_result_v1(
  p_batch_id uuid,
  p_claim_token uuid,
  p_path text,
  p_state text,
  p_result_ref text default null
)
returns jsonb
language sql
security definer
set search_path = pg_catalog, radar
as $$
  select to_jsonb(
    radar.projection_record_file_result_v1(
      p_batch_id,
      p_claim_token,
      p_path,
      p_state,
      p_result_ref
    )
  );
$$;

create or replace function public.radar_projection_finalize_batch_v1(
  p_batch_id uuid,
  p_claim_token uuid
)
returns jsonb
language sql
security definer
set search_path = pg_catalog, radar
as $$
  select to_jsonb(radar.projection_finalize_batch_v1(p_batch_id, p_claim_token));
$$;

revoke all on function public.radar_projection_get_lane_v1(text,text)
  from public, anon, authenticated;
revoke all on function public.radar_projection_set_control_cut_v1(text,text,text,text)
  from public, anon, authenticated;
revoke all on function public.radar_projection_claim_batch_v1(text,text,text,text,text,text,text[],text,integer)
  from public, anon, authenticated;
revoke all on function public.radar_projection_record_file_result_v1(uuid,uuid,text,text,text)
  from public, anon, authenticated;
revoke all on function public.radar_projection_finalize_batch_v1(uuid,uuid)
  from public, anon, authenticated;

grant execute on function public.radar_projection_get_lane_v1(text,text) to service_role;
grant execute on function public.radar_projection_set_control_cut_v1(text,text,text,text) to service_role;
grant execute on function public.radar_projection_claim_batch_v1(text,text,text,text,text,text,text[],text,integer) to service_role;
grant execute on function public.radar_projection_record_file_result_v1(uuid,uuid,text,text,text) to service_role;
grant execute on function public.radar_projection_finalize_batch_v1(uuid,uuid) to service_role;
