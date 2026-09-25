[Reading 89 lines from start (total: 89 lines, 0 remaining)]

begin;

insert into radar.identities(identity_id, display_name, aliases)
values ('provenance-test-controller', 'Provenance Test Controller', '{}')
on conflict (identity_id) do nothing;

insert into radar.messages(
  message_id, schema_version, created_at, sender, audience, domain, intent,
  priority, requires_ack, source_refs, content_hash, idempotency_key, payload
)
values (
  'provenance-test-root', 1, now(), 'provenance-test-controller',
  array['provenance-test-worker'], 'chat_bus', 'bus_message', 3, false,
  array['github://example/ccb-core/provenance-test-root'], repeat('a', 64),
  'provenance:test-root', jsonb_build_object('subject', 'immutable root controller')
);

update radar.messages
set projection_status = 'DELIVERING'
where message_id = 'provenance-test-root';

do $$
begin
  if not exists (
    select 1 from radar.messages
    where message_id = 'provenance-test-root'
      and projection_status = 'DELIVERING'
  ) then
    raise exception 'RADAR_TEST_PROJECTION_STATUS_NOT_MUTABLE';
  end if;
end
$$;

do $$
begin
  begin
    delete from radar.messages where message_id = 'provenance-test-root';
    raise exception 'RADAR_TEST_EXPECTED_MESSAGE_DELETE_IMMUTABLE';
  exception when sqlstate '55000' then
    if sqlerrm not like '%RADAR_MESSAGE_PROVENANCE_IMMUTABLE%' then
      raise;
    end if;
  end;
end
$$;

do $$
begin
  if not exists (
    select 1 from radar.messages
    where message_id = 'provenance-test-root'
      and sender = 'provenance-test-controller'
  ) then
    raise exception 'RADAR_TEST_MESSAGE_DELETE_GUARD_FAILED';
  end if;
end
$$;

do $$
begin
  begin
    update radar.messages
    set sender = 'provenance-test-intruder'
    where message_id = 'provenance-test-root';
    raise exception 'RADAR_TEST_EXPECTED_MESSAGE_PROVENANCE_IMMUTABLE';
  exception when sqlstate '55000' then
    if sqlerrm not like '%RADAR_MESSAGE_PROVENANCE_IMMUTABLE%' then
      raise;
    end if;
  end;
end
$$;

do $$
begin
  begin
    update radar.messages
    set payload = jsonb_build_object('subject', 'rewritten')
    where message_id = 'provenance-test-root';
    raise exception 'RADAR_TEST_EXPECTED_MESSAGE_PAYLOAD_IMMUTABLE';
  exception when sqlstate '55000' then
    if sqlerrm not like '%RADAR_MESSAGE_PROVENANCE_IMMUTABLE%' then
      raise;
    end if;
  end;
end
$$;

rollback;