begin;

do $$
begin
  if has_table_privilege('service_role', 'radar.messages', 'INSERT')
     or has_table_privilege('service_role', 'radar.messages', 'UPDATE')
     or has_table_privilege('service_role', 'radar.messages', 'DELETE')
  then
    raise exception 'RADAR_TEST_MESSAGES_DIRECT_DML_STILL_GRANTED';
  end if;
  if not has_table_privilege('service_role', 'radar.messages', 'SELECT') then
    raise exception 'RADAR_TEST_MESSAGES_SELECT_MISSING';
  end if;
  if not has_function_privilege(
    'service_role',
    'radar.project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb)',
    'EXECUTE'
  ) then
    raise exception 'RADAR_TEST_MESSAGE_PROJECTION_RPC_EXECUTE_MISSING';
  end if;
end
$$;

rollback;
