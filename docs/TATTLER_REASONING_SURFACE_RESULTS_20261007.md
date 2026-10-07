# Tattler reasoning-surface results — 2026-10-07

Status: OBSERVATION NOTE / BUS SEMANTIC BOUNDARY

## Shared experiment result

On 2026-10-07, the same repository stress-test prompt was run through three ChatGPT surfaces while WorkLaptop was instrumented with Tattler plus a companion Codex process/network tracer.

Observed controlled windows:

- Desktop Chat, GPT-5.6 Sol High: **0 MXC launches** and **2 new established Codex TLS connections** in the companion tracer.
- ChatGPT Desktop Work, Ultra: **59 MXC launches** and **73 new established Codex TLS connections** using the same companion-tracer definitions.
- Firefox cloud Work, Max: browser-side traffic was observable locally, but the provider's server-side worker topology was not.

The bounded conclusion is that Desktop Work used materially different local orchestration from ordinary High Chat in this runtime. It does **not** establish that sockets or MXC processes equal agents, that connection fanout grants a reasoning tier, or that a client can promote High into Ultra/Max by imitating transport behavior.

Canonical detailed evidence is being preserved in `thebrazenbeard/tattler` PR #7 and the reasoning interpretation in `thebrazenbeard/rezon` PR #103.


## Why CCB Base needs this result

CCB Base may transport assignments, review requests, results, and receipts between workers. The Tattler experiment shows that transport/runtime topology cannot be used as a proxy for reasoning identity or reasoning independence.

A Bus envelope may carry explicit fields such as product surface, model/reasoning label, task identity, or reviewer role when supplied by the sender. Those fields are assertions/provenance carried by the message; they are not inferred from connection count, node count, heartbeat count, or delivery path.

```text
delivery path != model identity
node/session count != reasoning-agent count
different route != independent reviewer
different reasoning label != proven independence
```

## Independence implication

The Ultra and Max outputs converged on central P.O.R.T.A.L. defects, which is useful evidence. But both were OpenAI Work surfaces and shared the same task/repository context. CCB should therefore preserve contamination/lineage metadata when transporting independent-review claims rather than treating distinct labels as sufficient independence proof.

## No routing promotion

This result does not justify moving reasoning-tier selection into CCB. Rezon remains the semantic owner of reasoning-worker eligibility; CCB remains a transport/coordination substrate.
