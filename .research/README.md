# Research and architecture

The current design is **v0.2**: a new Python agent with an isolated C++ memory engine, all **23 requested modules**, and **Agent Client Protocol** as the ACP boundary.

- [Visual module map — PNG](diagrams/v0.2/module-map.png) · [editable SVG](diagrams/v0.2/module-map.svg)
- [Complete module ownership and interaction map](agent-module-map-v0.2.md): each module's responsibility, owned records and boundaries; ACP, behavior, plugins, delegation and self-evolution flows.
- [Full architecture schematics](agent-architecture-v0.2.md): system topology, uniform tool lifecycle, labeled context, temporal memory/daily sleep, independent input/output and Python/C++ boundaries.
- [Interface and domain contracts](agent-interface-contracts-v0.2.md): events, calls/results, context packets, native IPC, delivery, profiles, attention, goals, waits, plans, extensions, child work and evaluated evolution.
- [Nine editable Mermaid diagrams](diagrams/v0.2/): runtime, context, memory, I/O, action lifecycle, behavior, ACP, capability/delegation and evolution.
- [Keith module reference](keith-module-reference-2026-09-12.md): source-grounded clarification from the user-supplied `/root/agent-keith`, with all 23 module anchors and explicit differences in the new proposal.
- [Current memory systems for agents — 12 September 2026](agent-memory-systems-2026-09-12.md): primary-source comparison of 13 systems, memory benchmarks and proposed evaluations.

The proposal uses requirements from this discussion, public research, and the current-source module clarification explicitly requested by the user. Prior project memories are excluded. Source observations, design choices and untested behavior are distinguished. Agent implementation has not started.

Historical drafts: [v0.1 architecture](agent-architecture-v0.1.md), [v0.1 contracts](agent-interface-contracts-v0.1.md), [v0.1 overview](diagrams/agent-overview.png). Module ownership in v0.2 supersedes the earlier draft.
