---
title: "Accessible components and browser verification"
description: "Preserve keyboard, focus, reduced motion and truthful asynchronous states."
---

Accessible behavior is part of the interaction contract. The client uses Radix for dialog, select, dropdown, tooltip and sheet behavior, then applies a consistent Material 3 visual system. Reusing the primitive preserves focus management and keyboard semantics while domain pages supply meaningful labels and error states.

## Worked review: configure a connection

Open the connection dialog using the keyboard. Confirm focus enters the dialog and its accessible name describes the task. Tab through labeled controls, choose the adapter kind, and verify conditional fields are introduced with understandable labels. Submit invalid JSON or a missing required operation and check that the error remains readable without losing the form's context.

Then submit valid configuration and run an actual probe. A successful save should not render readiness as verified until the probe establishes it. On failure, preserve the typed explanation and keep retry possible. Escape closes the dialog and returns focus to a meaningful trigger; one-time pairing secrets disappear when their result dialog closes.

## State and presentation

Buttons show pending state and avoid double submission. Empty collections explain what the user can create; unavailable runtime/provider data is not presented as an empty successful response. Conversation streaming distinguishes live, reconnecting and idle transport state and retains partial output after failure.

The responsive shell keeps navigation usable on narrow screens through a modal sheet. Motion respects reduced-motion preferences. Code blocks, filenames and long messages should remain readable without forcing horizontal page overflow. Color alone must not carry success, error or current selection.

Shared notifications in `frontend/src/App.tsx` retain full opacity during their 160 ms vertical entrance and exit. Their text must remain readable throughout the animation; fading an otherwise compliant foreground/background pair can temporarily reduce contrast. Success notifications use `role="status"`, errors use `role="alert"`, and each notification has a labeled dismiss button. Inspect actual animation frames when validating a notification change.

## Evidence

Component and page tests check real behavior and request shapes. The browser journey exercises the built client against the actual service, including desktop/mobile paths and accessibility inspection. Coverage alone cannot establish focus correctness or contrast, so inspect both the automated findings and real browser interaction.

Do not add untrusted raw HTML rendering to improve formatting. Model and source text are content, not application markup or authority. For downloads and generated media, retain the scoped artifact boundary.

See [frontend structure](structure.md), [data states](data-events.md) and [qualification](../getting-started/qualification.md).

## Implementation sources

- [frontend/src/App.tsx](/reference/files/frontend/src/App.tsx/)
- [frontend/src/components/ui.tsx](/reference/files/frontend/src/components/ui.tsx/)
- [frontend/src/components/Shell.tsx](/reference/files/frontend/src/components/Shell.tsx/)
- [frontend/src/components/components.css](/reference/files/frontend/src/components/components.css/)
- [frontend/src/pages/ConversationPage.tsx](/reference/files/frontend/src/pages/ConversationPage.tsx/)
- [frontend/src/pages/WorkPage.tsx](/reference/files/frontend/src/pages/WorkPage.tsx/)
- [frontend/src/pages/ConnectionsPage.tsx](/reference/files/frontend/src/pages/ConnectionsPage.tsx/)
- [frontend/src/pages/FilesPage.tsx](/reference/files/frontend/src/pages/FilesPage.tsx/)
- [frontend/src/pages/KnowledgePage.tsx](/reference/files/frontend/src/pages/KnowledgePage.tsx/)
- [frontend/src/pages/SettingsPage.tsx](/reference/files/frontend/src/pages/SettingsPage.tsx/)
- [tests/consumer_web/journey.mjs](/reference/files/tests/consumer_web/journey.mjs/)

[Developer manual](../index.md)
