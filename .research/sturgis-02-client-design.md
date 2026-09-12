# Sturgis consumer design direction

The owner's accepted direction is a Gemini-inspired Material 3 interface with Sturgis branding. Self-hosted operation receives the complete premium interface; hosted operation uses the same compiled client and authenticated API.

## Implementation

React 19.3 and TypeScript, Vite production builds, Tailwind 4, Radix accessibility primitives, Motion with reduced-motion preferences, scoped TanStack Query caching, locally served Roboto Flex typography, and Material Color Utilities generated semantic color roles. The Python server serves fingerprinted static assets; no Node service or vendor hosting account is required at runtime.

The shell contains one collapsible navigation drawer, recent conversations, and a calm header. The conversation has a spacious central canvas, a rounded filled composer, deliberate suggestion cards, clear response states and durable draft/retry behavior. Feature pages use the same tonal surfaces, rounded controls, dialogs, focus states and spacing scale. Secondary controls move into contextual menus or dialogs instead of competing with primary tasks.

## Source organization

`frontend/src/components/`: shared components and shell.
`frontend/src/lib/`: typed consumer contracts and network access.
`frontend/src/hooks/`: scoped queries, actions and replayable event subscription.
`frontend/src/pages/`: independently loaded feature surfaces.
`frontend/src/global.css`, `material.css`: shared typography, layout and semantic design tokens.
`consumer/web/`: generated, deployable static output.

## Qualification

All authored TypeScript/JavaScript remains in the executable coverage denominator, with a 90% floor. Real Chromium journeys exercise the installed production build against actual Python services/private workers/native memory and controlled wire providers. Component and hook tests add keyboard/focus, cancellation and failure-state evidence. Source maps tie browser coverage to the exact authored files and build hashes. Accessibility, narrow layouts, reduced motion, cold-load transfer size and input responsiveness are measured separately from coverage.

## Primary references consulted

- React release/version policy: https://react.dev/versions
- Vite production build options: https://vite.dev/config/build-options
- Rolldown chunk groups: https://rolldown.rs/reference/OutputOptions.codeSplitting
- Tailwind Vite integration: https://tailwindcss.com/docs/installation/using-vite
- Radix composition and accessibility primitives: https://www.radix-ui.com/primitives/docs/overview/introduction
- Material color roles: https://m3.material.io/styles/color/roles
- Material motion: https://m3.material.io/styles/motion/overview/how-it-works
- Material Color Utilities: https://github.com/material-foundation/material-color-utilities

Framework selection is an engineering choice for a portable static client; no framework is claimed to be universally best. The quality target is assessed through the actual resulting product and measurements.
