-- Radar visual identity V1 metadata projection.
-- Git remains canonical. This table stores no image bytes.

create table if not exists radar.identity_visuals (
  asset_id text primary key,
  identity_id text not null references radar.identities(identity_id) on delete cascade,
  scope text not null check (scope in ('portrait','bus_avatar','character')),
  state text not null check (state in ('PLACEHOLDER','CANDIDATE','CANONICAL','RETIRED')),
  repository_path text not null,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  claimed_by text not null,
  claim_type text not null check (claim_type in ('self','operator_override','system_placeholder')),
  claimed_at timestamptz not null,
  source_ref text not null,
  style_contract text not null check (style_contract = 'BUS_AVATAR_STYLE_V1'),
  supersedes text references radar.identity_visuals(asset_id) on delete restrict,
  retired_at timestamptz,
  review_after timestamptz,
  is_current boolean not null default false,
  is_active_candidate boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (not is_current or state = 'CANONICAL'),
  check (not is_active_candidate or state = 'CANDIDATE'),
  check (state <> 'RETIRED' or retired_at is not null)
);

create unique index if not exists radar_identity_visuals_current_scope_idx
  on radar.identity_visuals(identity_id, scope)
  where is_current;

create unique index if not exists radar_identity_visuals_active_candidate_scope_idx
  on radar.identity_visuals(identity_id, scope)
  where is_active_candidate;

create index if not exists radar_identity_visuals_identity_idx
  on radar.identity_visuals(identity_id, scope, state);

alter table radar.identity_visuals enable row level security;

revoke all on table radar.identity_visuals from public;
revoke all on table radar.identity_visuals from anon;
revoke all on table radar.identity_visuals from authenticated;
grant select, insert, update, delete on table radar.identity_visuals to service_role;
