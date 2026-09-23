-- Radar GitHub OIDC replay guard V1.
-- Source-only migration. A verified GitHub OIDC token is consumed exactly once
-- before any provider projection effect is attempted.

create table if not exists radar.github_oidc_replay_guard (
  jti text primary key,
  expires_at timestamptz not null,
  consumed_at timestamptz not null default now(),
  constraint radar_github_oidc_replay_guard_jti_nonempty check (btrim(jti) <> '')
);

create index if not exists radar_github_oidc_replay_guard_expiry_idx
  on radar.github_oidc_replay_guard(expires_at);

revoke all on table radar.github_oidc_replay_guard from public;
revoke all on table radar.github_oidc_replay_guard from anon;
revoke all on table radar.github_oidc_replay_guard from authenticated;

create or replace function radar.consume_github_oidc_jti_v1(
  p_jti text,
  p_expires_at timestamptz
)
returns text
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  inserted_jti text;
begin
  if p_jti is null or btrim(p_jti) = '' then
    raise exception 'OIDC_JTI_REQUIRED' using errcode = '22023';
  end if;
  if p_expires_at is null then
    raise exception 'OIDC_EXP_REQUIRED' using errcode = '22023';
  end if;
  if p_expires_at <= clock_timestamp() then
    raise exception 'OIDC_JTI_EXPIRED' using errcode = '22023';
  end if;

  -- JWT verification rejects expired tokens before this RPC. Rows that can no
  -- longer correspond to an accepted token therefore carry no replay value.
  delete from radar.github_oidc_replay_guard
  where expires_at <= clock_timestamp();

  insert into radar.github_oidc_replay_guard(jti, expires_at)
  values (btrim(p_jti), p_expires_at)
  on conflict (jti) do nothing
  returning jti into inserted_jti;

  if inserted_jti is null then
    return 'REPLAY';
  end if;
  return 'CONSUMED';
end;
$$;

revoke all on function radar.consume_github_oidc_jti_v1(text,timestamptz) from public;
revoke all on function radar.consume_github_oidc_jti_v1(text,timestamptz) from anon;
revoke all on function radar.consume_github_oidc_jti_v1(text,timestamptz) from authenticated;
grant execute on function radar.consume_github_oidc_jti_v1(text,timestamptz) to service_role;

-- PostgREST exposes the public schema. Keep the bridge narrow and executable
-- only by service_role; the authoritative state/function remain in radar.
create or replace function public.radar_consume_github_oidc_jti_v1(
  p_jti text,
  p_expires_at timestamptz
)
returns text
language sql
security invoker
set search_path = pg_catalog, radar
as $$
  select radar.consume_github_oidc_jti_v1(p_jti, p_expires_at);
$$;

revoke all on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) from public;
revoke all on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) from anon;
revoke all on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) from authenticated;
grant execute on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) to service_role;
