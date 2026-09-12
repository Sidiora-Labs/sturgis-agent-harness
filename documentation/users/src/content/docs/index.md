---
title: "Learn and use Sturgis"
description: "A complete learning path from your first agent to ongoing work, private knowledge, connections, and recovery."
---

The [complete command cookbook](reference/command-recipes.md) gives a worked example for every public domain operation.

Sturgis gives an agent a continuing place to work: conversations, a workspace, source material, memory, and recorded work. This manual explains the product you can use today. It marks features that require the CLI or an integration instead of implying that every backend capability has a web button.

<div class="docs-grid">
<a href="/getting-started/installation-choices/"><strong>Your first agent</strong><span>Choose an installation, create your private space and connect a model.</span></a>
<a href="/conversations/continuing-conversations/"><strong>Learn the conversation</strong><span>Send messages, add sources and steer ongoing work with confidence.</span></a>
<a href="/knowledge/source-library/"><strong>Work with your information</strong><span>Build a source library, refine memory and preserve useful files.</span></a>
<a href="/work/overview/"><strong>Make work continue</strong><span>Understand goals, schedules, workflows and recorded outcomes.</span></a>
</div>

## Choose your learning path

**I want my first useful conversation.** Start with [installation choices](getting-started/installation-choices.md), [your account and first agent](getting-started/account-and-agents.md), and [connecting a model](models/connect-a-model.md). Then follow [continuing conversations](conversations/continuing-conversations.md).

**I want an agent that works with my information.** Upload a small document in the [source library](knowledge/source-library.md), index it, and [attach it to a conversation](conversations/sources-history-search.md). Learn how to [correct memory](knowledge/memory.md) when information changes.

**I want work to continue over time.** Read [the Work overview](work/overview.md), then create a [goal](work/goals.md), [commitment](work/commitments.md), [schedule](work/schedules.md), or [workflow](work/workflows.md). These records serve different purposes; creating a goal alone does not schedule a model run.

**I want to connect services.** Begin with [connection access and readiness](connections/overview.md), then choose the specific adapter. A saved endpoint, a grant, a successful readiness check, and permission for an individual action are separate facts.

**I operate the installation.** Use [self-hosted installation](getting-started/self-hosted.md) or [hosted operation](getting-started/hosted.md), then review [lifecycle and recovery](settings/lifecycle.md), [backups](settings/backup-restore.md), and [troubleshooting](reference/troubleshooting.md).

## Worked example: a research companion

Create an agent called **Research companion**. Connect a model that supports the protocol supplied by your service. Upload `observatory-notes.md`, add it to knowledge, and ask for a source-based summary. Save your own edited summary in Files. When a source changes, correct the relevant memory record instead of treating the previous wording as permanent truth. Finally, export a backup and restore it into a separate agent.

The expected result is a visible chain of source material, conversation history, file content, and memory revisions. A generated summary is still a model response; it does not establish that an external task succeeded.

## Conventions used throughout

UI labels appear in **bold**. `sturgis command` examples use the same authenticated service as the browser. Replace uppercase sample IDs such as `AGENT_ID` with values returned by your installation, and refresh revision numbers before changing existing records. JSON blocks labeled with a filename are saved to that file before running the accompanying command.

Read [CLI essentials](cli/using-the-cli.md) before copying command examples. The [command reference](reference/commands.md), [limits](reference/limits.md), and [glossary](reference/glossary.md) are useful while you work. For an interrupted action, preserve its identity and inspect its recorded state before retrying; see [steering and cancellation](conversations/steering-and-cancellation.md).

## Recognize your workspace

![The Sturgis web client: a spacious conversation canvas, private navigation and a persistent composer](/images/conversation.png)
