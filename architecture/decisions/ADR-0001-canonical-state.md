# ADR-0001 — GitHub is Radar canonical durable state

Status: ACCEPTED — 2026-09-02

Radar treats this GitHub repository as durable canonical source/provenance. Supabase and ChatGPT are projections/interfaces. A fresh Radar session must be able to reconstruct core architecture, protocols, source, migration custody, and repository state from GitHub without trusting a prior chat transcript.

Consequences: provider state is read back and reconciled; Supabase loss is recoverable; durable protocol changes are committed; live projections never silently overwrite GitHub history.