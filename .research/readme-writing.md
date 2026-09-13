# README writing research

Reviewed 2026-09-13 for the Sturgis repository introduction. This is an editorial
record; the project README should not contain this research or implementation
session history.

## Guidance read

- [GitHub: About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
  centers the project purpose, usefulness, first steps, help and maintainers. It
  recommends keeping the entrypoint focused and using separate documentation for
  material beyond getting started.
- [GitHub Open Source Guides: Starting a project](https://opensource.guide/starting-a-project/#writing-a-readme)
  treats the README as an explanation of why a project matters. It specifically
  calls for stating when a project is not ready for production and linking to
  contribution guidance.
- [Danny Guo: Make a README](https://www.makeareadme.com/)
  recommends specific capabilities, differentiating factors, explicit
  prerequisites, a small useful example and appropriate visuals. Its section
  suggestions should be adapted to the project rather than filled mechanically.
- [Write the Docs: How to write software documentation](https://www.writethedocs.org/guide/writing/beginners-guide-to-docs/)
  starts with audience and the problem being solved. It recommends a short
  installation path with deeper instructions elsewhere and warns that unrelated
  FAQ content accumulates easily.
- [GDS README template](https://github.com/alphagov/readme-template)
  connects the introduction to actual users, makes limitations visible early,
  and places copyable first-run steps after prerequisites.

## Examples inspected

- [Temporal](https://github.com/temporalio/temporal/blob/main/README.md)
  names its organizing concept immediately, explains the execution mechanism,
  then gives a brief local start and links to samples and contributor material.
- [uv](https://github.com/astral-sh/uv/blob/main/README.md)
  introduces a precise tool category and its distinguishing claim, links that
  claim to evidence, and demonstrates use through short command/output examples.
  Sturgis has no comparable benchmark to advertise; its architectural choices
  can be explained directly without inventing performance or maturity claims.

## Application to Sturgis

The intended audience is someone evaluating a new agent architecture and deciding
whether to try or contribute to it. A generic list of chat, files, models and
tasks fails to explain the design.

Lead with the continuing digital environment: identity, observations, goals,
commitments, action outcomes and memory persist independently of individual model
invocations. Explain the mechanisms that make this concrete: canonical action
contracts, labeled context with inclusion manifests, temporal concept-based
memory and daily consolidation, independent input/output lanes, and delegated
work governed by shared authority.

Keep the active-development/not-production warning prominent. Describe the
architecture as experimental; do not claim consciousness, persistent neural
state, continuous self-improvement, universal provider compatibility, superior
performance, or production reliability.

Show one compact conceptual diagram, then the actual root development commands.
Explain the expected local URL and first account/model connection. Move command
catalogs, lint adoption policy, module inventories and engineering process to
CONTRIBUTING and the developer documentation. The root README must not narrate
spec task numbers, debugging sessions, or qualification receipts.
