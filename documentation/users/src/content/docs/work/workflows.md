---
title: "Run workflows and revise plans"
description: "Execute dependent steps with budgets, inspect their completion basis, and safely revise or resume a plan."
---

A workflow is a versioned plan whose steps have dependencies, allowances, and completion checks. It is useful when one result is required before another step can run. The UI creates a sequential plan; the CLI/API supports a more expressive dependency graph.

## Create and run a sequential workflow

Open **Work → Workflows → New workflow**. In **Workflow steps**, enter one step per line. Review the shared **Compute allowance**, then save. The UI accepts one to 64 nonempty lines and gives each step an equal integer share of the allowance. Allow at least one token and one model turn per step.

Choose **Run workflow** to admit execution. Creation alone does not run the plan. The UI uses the current work conversation selection/default, creating **Ongoing work** if necessary; the CLI/API lets you specify the conversation explicitly.

## Worked example: draft and review

Enter these three lines:

```text
Read the uploaded maintenance notes and identify supported dates.
Draft a short brief separating evidence from uncertainty.
Review the brief against the source and list remaining gaps.
```

Use an allowance large enough for three model turns. Expect the second step to wait for the first, and the third to wait for the second. Expand **Inspect evidence** to inspect the plan and execution attempts.

For CLI inspection, save `plan.json` with `{"id":"PLAN_ID"}`:

```sh
sturgis command AGENT_ID workflows.list
sturgis command AGENT_ID workflows.get --params-file plan.json
```

To run, save `run-plan.json`:

```json
{"id":"PLAN_ID","conversation_id":"CONVERSATION_ID","expected_revision":1}
```

```sh
sturgis command AGENT_ID workflows.run --params-file run-plan.json
```

Use the actual current revision. A plan cannot switch its execution conversation after an execution run has been recorded.

## Advanced plans through CLI/API

`workflows.create` accepts `steps`, `budget`, and optional `goal_id`/`commitment_id`. Each step can specify `id`, `description`, `dependencies`, `assignee`, `checks`, `expectation`, and `budget`. The API bounds plans to 256 steps and rejects cycles or missing dependencies. Assignees can be the agent, a user, or an identified child.

`workflows.revise` requires the full revised step list, current revision, and reason; revise only a live plan without in-flight steps. Unchanged steps retain their state and evidence. `workflows.state` pauses, resumes, or otherwise follows the permitted plan transitions. `workflows.step` records a permitted step transition with evidence/reason when required; it is not a general bypass for the scheduler.

## Worked example: record your own review

Manual `in_progress` and `completed` transitions require a user-assigned step. Create a separate plan through the CLI with `human-review.json`:

```json
{"steps":[{"id":"human_review","description":"Read the brief and identify unsupported dates","assignee":{"kind":"user"},"dependencies":[],"budget":{}}],"budget":{}}
```

```sh
sturgis command AGENT_ID workflows.create --params-file human-review.json
```

Save the returned plan ID as `USER_PLAN_ID`. When you actually begin reviewing, submit `workflows.step` with `{"id":"USER_PLAN_ID","step_id":"human_review","state":"in_progress","expected_revision":1}` using the current revision. The step now records your review as in progress. It does not launch an agent model turn. A later completion must meet the step's configured checks. For agent-assigned steps, use `workflows.run`; manually marking them in progress or complete is rejected.

## Completion and recovery

The UI's default check establishes completion of a model turn with external outcome unasserted. For a real world outcome, supply explicit outcome checks through the API and verify supporting observations. The returned `completion_basis` distinguishes those cases.

If work blocks, inspect its attempt and dependency instead of resubmitting the whole plan. **Pause workflow** fences further assignments and requests cancellation of active work. If you paused before a step was admitted, **Resume workflow**, then **Run workflow**, can admit its pending steps.

An interrupted agent step becomes blocked. Resume and Run do not reset it: only pending steps with completed dependencies are ready. Inspect the previous attempt and any unknown effects first. Through `workflows.revise`, change the affected step definition and explain the correction; an unchanged definition retains its blocked state. Wait for in-flight work to settle before revising, and account for cumulative allocations already charged to the plan. A new definition does not replenish its allowance or prove that the interrupted action had no effect.

Abandoning a live plan is available through `workflows.state` with `state:"abandoned"` and a reason, not a dedicated web button. Resource exhaustion, missing permissions, and unknown effects remain visible. See [evidence](evidence.md) before resuming a blocked step or changing what “complete” means.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `workflows.create` | `{"steps":[{"id":"review","description":"Review the source","dependencies":[],"budget":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":2}}],"budget":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":2}}` |
| `workflows.get` | `{"id":"PLAN_ID"}` |
| `workflows.list` | `{}` |
| `workflows.revise` | `{"id":"PLAN_ID","expected_revision":1,"steps":[{"id":"review","description":"Review the corrected source","budget":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":2}}],"reason":"Source corrected"}` |
| `workflows.run` | `{"id":"PLAN_ID","conversation_id":"CONVERSATION_ID","expected_revision":1}` |
| `workflows.state` | `{"id":"PLAN_ID","state":"paused","expected_revision":2}` |
| `workflows.step` | `{"id":"USER_PLAN_ID","step_id":"human_review","state":"in_progress","expected_revision":1}` |
