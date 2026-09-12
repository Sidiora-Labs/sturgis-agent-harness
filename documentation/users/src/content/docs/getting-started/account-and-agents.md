---
title: "Create your account and first agent"
description: "Bootstrap or sign in, create private agent spaces, switch between them, and understand agent profiles."
sidebar:
  order: 4
---

An account owns agents. An agent owns its conversations, knowledge, workspace, memory, credentials, and work. Choose an agent before changing any of those resources; a similar display name does not make two agents the same space.

## Sign in or claim the first owner

On a new installation, the operator supplies the private installation token from `bootstrap.token`. Enter it with your email and a password of at least 12 characters in the first-owner form. A successful bootstrap creates the owner and signs you in immediately.

On later visits, use **Sign in**. Additional registration appears only when the operator enables signup. The account menu contains sign-out. Signing out revokes that session; it does not delete agents or automatically cancel already accepted work.

For CLI access:

```sh
sturgis --url http://127.0.0.1:8787 login --bootstrap --email you@example.com
```

The CLI privately prompts for the password and bootstrap token. Omit `--bootstrap` for normal login. There are no password or installation-token command-line flags. Do not put either value in shell history.

## Worked example: separate research from operations

Create an agent named **Research companion** using the agent creation control. Create a second agent named **Operations notes**. Use **Switch agent** in the sidebar to select each one. Upload a source in Research companion, then switch to Operations notes: it should not appear in that agent's library.

Equivalent CLI commands are:

```sh
sturgis agents create "Research companion"
sturgis agents create "Operations notes"
sturgis agents list
sturgis agents get AGENT_ID
```

Save the returned IDs. Names are display labels; IDs identify the owned resources used by commands and downloads. The sidebar opens Conversation, Work, Knowledge, Files, Connections, and Settings. `Ctrl+K` or `Cmd+K` opens the view selector. Its search finds views, not all conversation content.

## Rename, archive, and profile boundaries

Use **Settings → Agent & data → Rename** to change the display name. CLI rename requires the current metadata revision:

```sh
sturgis agents rename AGENT_ID "Research archive" --revision 1
```

Replace `1` with the revision you just read. Agent archive is an execution fence, not deletion; it has no unarchive action in this release. See [runtime lifecycle](../settings/lifecycle.md) before archiving.

The agent's private runtime profile and paths are assigned by the installation. There is no public profile-root editor, arbitrary scope switch, or per-command deployment-mode override. You can keep several provider configurations inside one agent, but those are model configurations, not additional agent identities.

## Recovery and limits

An authorization failure for another account's agent is expected, including for administrators. If a rename conflicts, reread the agent and apply the intended change to its current revision. If signup is unavailable, contact the operator; retrying bootstrap after an owner exists will not create another first owner. This release does not expose an account password-reset or invitation-management workflow.
