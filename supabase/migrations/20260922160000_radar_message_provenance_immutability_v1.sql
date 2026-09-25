[Reading 29 lines from start (total: 29 lines, 0 remaining)]

-- Keep projected message provenance append-only.
-- projection_status is the only mutable canonical-message field.

create or replace function radar.prevent_message_provenance_mutation_v1()
returns trigger
language plpgsql
set search_path = pg_catalog, radar
as $$
begin
  if TG_OP = 'DELETE' then
    raise exception 'RADAR_MESSAGE_PROVENANCE_IMMUTABLE' using errcode = '55000';
  end if;
  if (to_jsonb(new) - 'projection_status')
     is distinct from
     (to_jsonb(old) - 'projection_status')
  then
    raise exception 'RADAR_MESSAGE_PROVENANCE_IMMUTABLE' using errcode = '55000';
  end if;
  return new;
end;
$$;

drop trigger if exists radar_messages_provenance_immutable_v1 on radar.messages;
create trigger radar_messages_provenance_immutable_v1
before update or delete on radar.messages
for each row execute function radar.prevent_message_provenance_mutation_v1();

revoke all on function radar.prevent_message_provenance_mutation_v1()
  from public, anon, authenticated;