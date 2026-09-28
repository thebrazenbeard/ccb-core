# CCB Base Repair Checkpoint — 2026-09-28

Canonical base:

`thebrazenbeard/ccb-core@81254afff7fa6a44ea1b32fcc5f114909061d12f`

Repository role at this checkpoint:

- CCB Base is the reusable implementation authority for Chat Communication Bus
  and Radar mechanisms.
- `chat-communication-bus` is the private branch/deployment overlay and is not
  a second implementation authority.
- Public CCB Base entrypoints require explicit private topology/cutover inputs
  and do not assume private deployment files exist in this repository.
- Provider deployment, credentials, route activation, and private branch state
  remain outside source authority.

This checkpoint exists to run the current source through the repository's own
Python 3.11/3.12 CI and dependency-review gates. A passing result qualifies the
source tree; it does not claim a deployed Supabase/provider runtime or private
overlay state.
