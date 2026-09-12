---
title: "Schedule prompts and recurring work"
description: "Create one-time, interval, or calendar prompts with explicit conversations, resource bounds, and missed-run behavior."
---

A schedule admits a prompt into a selected conversation when it becomes due. Each occurrence has a bounded resource allocation and durable identity. Use a schedule for future work; use a [commitment](commitments.md) when you need to track a responsibility independently of execution time.

## Create a schedule in the web interface

Open **Work → Schedules** and choose **Schedule prompt**. Enter the prompt, select its conversation, choose **Once** or **Repeating interval**, and pick a future **Start time**. For an interval, enter **Repeat every (minutes)**. Review **Compute allowance** before saving.

The UI converts its local date/time input to a due instant. If no conversation exists, it can create **Ongoing work**. Selecting a conversation explicitly makes the intended destination easier to verify.

## Worked example: a weekday review

For a simple one-time check, select tomorrow morning in the UI and use “Review the source notes and list questions that still need an answer.” Expect a schedule row with a visible next run; when due, its output returns to the selected conversation.

For a timezone-aware weekday schedule, use the CLI/API. Save `weekday.json`:

```json
{"conversation_id":"CONVERSATION_ID","text":"Review the maintenance notes and report unresolved questions.","spec":{"kind":"calendar","timezone":"Europe/Berlin","hour":9,"minute":0,"weekdays":[0,1,2,3,4],"fold":"first"},"resources":{"tokens":64000,"iterations":8,"elapsed_ms":120000,"tool_calls":8},"missed_policy":"run_once"}
```

```sh
sturgis command AGENT_ID schedules.create --params-file weekday.json
sturgis command AGENT_ID schedules.list
```

Weekdays use Monday `0` through Sunday `6`. The calendar form is CLI/API-only. It requires an IANA timezone and an explicit daylight-saving fold policy: `first`, `second`, or `both`. Nonexistent local times at a spring-forward gap are skipped.

## Pause, resume, and missed runs

Use **Pause schedule**, **Resume schedule**, or **Cancel schedule** on a live row. The command form is `schedules.state` with `id`, `state` (`paused`, `active`, or `cancelled`), and `expected_revision`. Cancelling is terminal; create a new schedule for a different future intention.

The API supports `once` with `at`, `interval` with `anchor` and `every_seconds`, and the local weekday calendar. Intervals must be at least one second through the API; the UI uses whole minutes.

The CLI/API `missed_policy` chooses what happens after downtime:

| Policy | Catch-up behavior |
| --- | --- |
| `skip` | Skip overdue occurrences and move on to the next future occurrence. |
| `run_once` | Admit one catch-up occurrence; this is the normal consumer default. |
| `replay_bounded` | Admit at most 10 missed occurrences in a catch-up pass, then move to the next future occurrence. |

The consumer inherits the scheduler's 10-occurrence replay cap and does not expose a `max_replay` parameter. Choose a policy deliberately; downtime is not a promise to replay an unlimited backlog.

## Recovery and limits

An idle suspended agent retains its next scheduled wake. Archiving the agent fences that wake. Due work still needs a ready provider, resources, and permissions; a due timestamp is not proof of a successful model result.

If a schedule fails, inspect its conversation and [Work evidence](evidence.md). Refresh the row before a revisioned state change. Restoring a backup does not reactivate schedules: reconnect dependencies and create the desired schedules intentionally. Avoid creating another schedule just because an acknowledgement was lost; inspect the original request and recorded schedule first.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `schedules.create` | `{"conversation_id":"CONVERSATION_ID","text":"Review maintenance notes","spec":{"kind":"calendar","timezone":"Europe/Berlin","hour":9,"minute":0,"weekdays":[0,1,2,3,4],"fold":"first"},"resources":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":2}}` |
| `schedules.list` | `{}` |
| `schedules.state` | `{"id":"SCHEDULE_ID","state":"paused","expected_revision":1}` |
