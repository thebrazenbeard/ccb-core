[Reading 4 lines from start (total: 4 lines, 0 remaining)]

-- Require projected Bus message mutation through reviewed projection functions.

revoke insert, update, delete on table radar.messages from service_role;
grant select on table radar.messages to service_role;