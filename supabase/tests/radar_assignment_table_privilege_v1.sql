[Reading 31 lines from start (total: 31 lines, 0 remaining)]

begin;

do $$
begin
  if has_table_privilege('service_role', 'radar.assignments', 'INSERT')
     or has_table_privilege('service_role', 'radar.assignments', 'UPDATE')
     or has_table_privilege('service_role', 'radar.assignments', 'DELETE')
  then
    raise exception 'RADAR_TEST_ASSIGNMENTS_DIRECT_DML_STILL_GRANTED';
  end if;
  if not has_table_privilege('service_role', 'radar.assignments', 'SELECT') then
    raise exception 'RADAR_TEST_ASSIGNMENTS_SELECT_MISSING';
  end if;
  if has_table_privilege('service_role', 'radar.assignment_events', 'INSERT')
     or has_table_privilege('service_role', 'radar.assignment_events', 'UPDATE')
     or has_table_privilege('service_role', 'radar.assignment_events', 'DELETE')
  then
    raise exception 'RADAR_TEST_ASSIGNMENT_EVENTS_DIRECT_DML_STILL_GRANTED';
  end if;
  if not has_table_privilege('service_role', 'radar.assignment_events', 'SELECT') then
    raise exception 'RADAR_TEST_ASSIGNMENT_EVENTS_SELECT_MISSING';
  end if;
  if not has_function_privilege(
    'service_role', 'radar.reconcile_bus_assignments_v1()', 'EXECUTE'
  ) then
    raise exception 'RADAR_TEST_RECONCILE_EXECUTE_MISSING';
  end if;
end
$$;

rollback;