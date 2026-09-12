---
title: "Build with Sturgis"
description: "Understand the complete agent, extend its mechanisms and trace every important implementation contract."
---

<p class="docs-kicker">Sturgis · Developer documentation</p>
<p class="docs-lead">From a durable event to an observed effect. Learn the architecture, follow the contracts, and inspect the source that makes each mechanism work.</p>

<div class="docs-grid">
<a href="/guides/getting-started/setup/"><strong>Set up a development checkout</strong><span>Build the Python runtime, native worker and clients, then run real qualification.</span></a>
<a href="/guides/architecture/causality/"><strong>Follow the agent's execution</strong><span>Trace input, context, authority, effects, observations and delivery.</span></a>
<a href="/guides/integrations/tool-authoring/"><strong>Add a capability</strong><span>Implement the validated tool contract and preserve effect certainty.</span></a>
<a href="/reference/files/agent/runtime/__init__.py/"><strong>Read the implementation</strong><span>Inspect qualified definitions, contracts, control paths and every source line.</span></a>
</div>

## The architecture in one path

A client authenticates at the consumer boundary. Accepted input enters a private runtime with fixed ownership and a durable journal. Context is assembled into labeled packets; the scheduler grants bounded work; the provider streams observations and proposed actions. The tool runner validates authority, records effects and receipts, and delivery preserves output for the client. Native memory runs in a separate C++ process with explicit generations and bounded requests.

The runtime's journal is the operational authority. Retrieved memory, model prose and historical imported records are evidence with provenance; none can silently grant new execution authority.

![The Sturgis continuity loop, from authenticated admission through state, context, scheduling, effects and delivery](/diagrams/continuity.svg)

## Read the system at three levels

[Architecture and development guides](/guides/) explain responsibilities, invariants, extension patterns, failure modes and worked examples. **Command and protocol references** describe exact caller-facing inputs and outcomes. **The complete source reference** exposes every first-party file, qualified definition and numbered source line, with signatures and syntax-derived facts linked to their evidence.

Generated references identify what code declares and does syntactically. The authored guides explain why boundaries exist. Runtime qualification separately establishes the behaviors that were exercised.

## Start from your task

Build an isolated checkout to explore the runtime. Implement a tool to extend available actions. Add a provider or channel adapter to connect another service. Follow storage and recovery contracts before changing durable state. Use the consumer and frontend guides to add a feature across authentication, the command envelope, private IPC and the browser's query boundaries.
