-- Enforce assignment/event mutation through reviewed SECURITY DEFINER functions.
-- service_role may observe projection state but may not write these tables directly.

revoke insert, update, delete on table radar.assignments from service_role;
grant select on table radar.assignments to service_role;

revoke insert, update, delete on table radar.assignment_events from service_role;
grant select on table radar.assignment_events to service_role;
