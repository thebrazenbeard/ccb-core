-- Defense-in-depth RLS hardening for Radar GitHub OIDC replay guard.
--
-- The V1 replay-guard migration already revokes all direct table privileges
-- from PUBLIC, anon, and authenticated and exposes only service-role-only RPCs.
-- Enabling RLS adds a second table-level protection layer without introducing
-- permissive policies or changing the existing RPC authority model.
--
-- Deliberately do NOT FORCE ROW LEVEL SECURITY: the SECURITY DEFINER owner
-- function remains the controlled write path.

alter table radar.github_oidc_replay_guard enable row level security;

revoke all on table radar.github_oidc_replay_guard from public;
revoke all on table radar.github_oidc_replay_guard from anon;
revoke all on table radar.github_oidc_replay_guard from authenticated;

revoke all on function radar.consume_github_oidc_jti_v1(text,timestamptz) from public;
revoke all on function radar.consume_github_oidc_jti_v1(text,timestamptz) from anon;
revoke all on function radar.consume_github_oidc_jti_v1(text,timestamptz) from authenticated;
grant execute on function radar.consume_github_oidc_jti_v1(text,timestamptz) to service_role;

revoke all on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) from public;
revoke all on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) from anon;
revoke all on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) from authenticated;
grant execute on function public.radar_consume_github_oidc_jti_v1(text,timestamptz) to service_role;

comment on table radar.github_oidc_replay_guard is
  'Single-use GitHub OIDC JTI replay guard. RLS is enabled with no permissive policies; direct client table privileges are revoked. Controlled consumption remains service-role-only through the bound RPC.';
