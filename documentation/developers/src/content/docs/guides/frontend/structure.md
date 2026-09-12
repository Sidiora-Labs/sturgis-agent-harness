---
title: "Frontend architecture and page ownership"
description: "Keep the React client a projection over the existing consumer API."
---

The frontend uses React and TypeScript, Vite for building, React Query for server data, Radix behavior primitives, Motion and Material 3 styling. Roboto Flex and other required assets are bundled locally. Python serves the compiled output; the installed application does not need a Node runtime server.

App owns authentication, selected agent, top-level navigation, the shared query client and command palette. Shell owns the responsive navigation and conversation history surface. Pages own their domain interaction: Conversation, Work, Knowledge, Files, Connections and Settings.

Shared UI primitives provide buttons, inputs, cards, badges, dialogs, tabs, selects, tooltips and dropdowns. Domain validation stays in the service/page path rather than being hidden inside a visual Card.

## Example: add a bounded inspection panel

1. Define the response projection in `lib/types.ts` when it is shared.
2. Add the read operation to the existing application's explicit HTTP contract if it does not already exist.
3. Use `useAgentQuery` inside the active AgentContext.
4. Render a real loading, unavailable, empty and populated state.
5. Use a shared Modal for an action requiring input, then call `useAction.execute`.
6. Verify keyboard focus and the actual command parameters in tests.

Do not add an independent fetch cache, session store or worker transport in a page. It would bypass agent scoping, lifecycle fencing and mutation recovery.

## Component lifetime

Agent selection can change while asynchronous work is pending. Capture the request's account/agent context and isolate page state by its selected agent. A response from a prior selection must not mutate a different agent's form or overwrite its conversation draft.

The sidebar is one responsive navigation surface. Mobile uses a modal sheet rather than a second independently stateful navigation tree. New conversation and recent conversation selection use the same underlying conversation records as the conversation page.

Build-time TypeScript types are not runtime validation of server or remote data. The HTTP envelope and domain service validation remain authoritative. See [data and events](data-events.md), [recovery](mutation-recovery.md), and [accessibility](accessibility.md).

## Styles, semantic roles and responsive behavior

[`frontend/src/global.css`](/reference/files/frontend/src/global.css/#L1) is the entry stylesheet. It imports Tailwind, the local variable font and the Material role definitions, then supplies the document defaults, shared surfaces, focus rings and shell layouts. Its reduced-motion rule and narrow-layout breakpoints apply across feature pages. Keep shared keyboard visibility here; a feature-specific selector must not remove it.

[`frontend/src/material.css`](/reference/files/frontend/src/material.css/#L1) defines Sturgis's Material 3 roles from the product seed color. Pair a surface or container role with its corresponding foreground role rather than assuming white text always has contrast. The same file declares motion durations and easing. These tokens describe the product client; the documentation sites have their own shared theme file and do not import the application's CSS bundle.

[`frontend/src/pages/conversation.css`](/reference/files/frontend/src/pages/conversation.css/#L1) owns the conversation rail, transcript and composer layout. Its grid uses a shrinkable content column and viewport-relative height; later rules adapt the premium conversation surface and narrow layouts. Check the persistent composer with both long text and small viewports when changing these rules.

[`frontend/src/pages/work-connections.css`](/reference/files/frontend/src/pages/work-connections.css/#L1) owns the Work and Connections metrics, tabs, cards and forms, including their responsive arrangements. Shared primitive styles remain in `components/components.css`. A new inspection panel should reuse these owners, then verify focus, overflow and reduced-motion behavior in the browser. A successful CSS build alone cannot establish readable contrast or a usable mobile layout.

## Implementation sources

- [frontend/src/main.tsx](/reference/files/frontend/src/main.tsx/)
- [frontend/src/App.tsx](/reference/files/frontend/src/App.tsx/)
- [frontend/src/components/Shell.tsx](/reference/files/frontend/src/components/Shell.tsx/)
- [frontend/src/components/ui.tsx](/reference/files/frontend/src/components/ui.tsx/)
- [frontend/src/lib/types.ts](/reference/files/frontend/src/lib/types.ts/)
- [frontend/src/pages/shared.tsx](/reference/files/frontend/src/pages/shared.tsx/)
- [frontend/vite.config.ts](/reference/files/frontend/vite.config.ts/)

[Developer manual](../index.md)
