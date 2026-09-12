import { useState, type FormEvent, type ReactNode } from "react";
import {
  ArrowRight,
  CalendarClock,
  Check,
  Circle,
  Clock3,
  Flag,
  GitBranch,
  Inbox,
  Layers2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Target,
  X,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Modal,
  Select,
  Skeleton,
  Tabs,
  Textarea,
  type BadgeTone,
} from "../components/ui";
import { useAction, useAgentQuery } from "../hooks/useAgent";
import type { Conversation, Page, Params } from "../lib/types";
import "./work-connections.css";

type Row = { id: string; revision: number; state: string };
type Goal = Row & { objective: string; archived?: boolean; limits?: Params };
type Commitment = Row & {
  description: string;
  progress: string;
  beneficiary: string;
  wait_id: string | null;
};
type Schedule = Row & { payload: { text?: string }; next_due: number | null };
type Workflow = Row & {
  steps: Array<{ id: string; description: string; state: string }>;
  execution?: unknown;
};
type Work = Row & { kind: string; objective?: string };
type Proposal = {
  id: string;
  decision: string;
  candidate: { action: string | { text?: string } };
};
type WorkData = {
  goals: Goal[];
  commitments: Commitment[];
  schedules: Schedule[];
  workflows: Workflow[];
  work: Work[];
  children: Work[];
  inbox: Proposal[];
};
type Editor = {
  kind: "goal" | "commitment" | "schedule" | "workflow";
  goal?: Goal;
} | null;

const tabs = [
  "Overview",
  "Goals",
  "Commitments",
  "Schedules",
  "Workflows",
  "Inbox",
].map((value) => ({ value, label: value }));
const activeWork = new Set(["queued", "running", "waiting"]);
const terminalGoals = new Set(["achieved", "failed", "abandoned"]);
const tone = (state: string): BadgeTone =>
  ["active", "running", "accepted", "ready"].includes(state)
    ? "accent"
    : ["completed", "achieved", "fulfilled", "started"].includes(state)
      ? "success"
      : ["blocked", "paused", "waiting", "pending"].includes(state)
        ? "warning"
        : ["failed", "cancelled", "abandoned"].includes(state)
          ? "danger"
          : "neutral";
const titleCase = (value: string) =>
  value.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());

function Evidence({ value }: { value: unknown }) {
  return (
    <details className="details">
      <summary>Inspect evidence</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
function WorkRow({
  icon,
  title,
  state,
  subtitle,
  actions,
  children,
}: {
  icon: ReactNode;
  title: string;
  state: string;
  subtitle?: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <article className="wc-record">
      <span className="wc-record-icon" aria-hidden="true">
        {icon}
      </span>
      <div className="wc-record-body">
        <div className="wc-record-heading">
          <h3>{title}</h3>
          <Badge tone={tone(state)}>{titleCase(state)}</Badge>
        </div>
        {subtitle && <p className="record-subtitle">{subtitle}</p>}
        {actions && <div className="record-actions">{actions}</div>}
        {children}
      </div>
    </article>
  );
}
function Budget({
  tokens = 64000,
  iterations = 8,
  seconds = 120,
  calls = 8,
}: {
  tokens?: number;
  iterations?: number;
  seconds?: number;
  calls?: number;
}) {
  return (
    <details className="wc-budget">
      <summary>Compute allowance</summary>
      <p className="field-hint">
        Maximum resources this work may use. Actual consumption stays visible in
        Settings.
      </p>
      <div className="form-row">
        <label className="field">
          Tokens
          <Input
            type="number"
            name="tokens"
            min={1}
            step={1}
            defaultValue={tokens}
            required
          />
        </label>
        <label className="field">
          Model turns
          <Input
            type="number"
            name="iterations"
            min={1}
            max={256}
            step={1}
            defaultValue={iterations}
            required
          />
        </label>
        <label className="field">
          Time in seconds
          <Input
            type="number"
            name="seconds"
            min={1}
            max={86400}
            step={1}
            defaultValue={seconds}
            required
          />
        </label>
        <label className="field">
          Tool calls
          <Input
            type="number"
            name="tool_calls"
            min={0}
            max={10000}
            step={1}
            defaultValue={calls}
            required
          />
        </label>
      </div>
    </details>
  );
}
function resources(data: FormData) {
  return {
    tokens: Number(data.get("tokens")),
    iterations: Number(data.get("iterations")),
    elapsed_ms: Number(data.get("seconds")) * 1000,
    tool_calls: Number(data.get("tool_calls")),
  };
}

export default function WorkPage() {
  const query = useAgentQuery<WorkData>(
    "work.overview",
    {},
    { interval: 5000 },
  );
  const rooms = useAgentQuery<Page<Conversation>>("conversations.list");
  const { execute, pending } = useAction();
  const [tab, setTab] = useState("Overview");
  const [editor, setEditor] = useState<Editor>(null);
  const [error, setError] = useState("");
  const [conversationId, setConversationId] = useState("automatic");
  const [scheduleKind, setScheduleKind] = useState("once");
  function open(next: Editor) {
    setError("");
    setConversationId("automatic");
    setScheduleKind("once");
    setEditor(next);
  }
  async function conversation() {
    if (conversationId !== "automatic") return conversationId;
    return (
      rooms.data?.items[0]?.id ||
      (
        await execute<Conversation>("conversations.create", {
          title: "Ongoing work",
        })
      )?.id
    );
  }
  async function runWorkflow(plan: Workflow) {
    const conversation_id = await conversation();
    if (conversation_id)
      await execute(
        "workflows.run",
        { id: plan.id, conversation_id, expected_revision: plan.revision },
        "Workflow admitted. Progress follows its execution evidence.",
      );
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editor || pending) return;
    const form = new FormData(event.currentTarget);
    setError("");
    let result: unknown;
    if (editor.kind === "goal") {
      const params = {
        objective: String(form.get("objective")),
        limits: { ...editor.goal?.limits, ...resources(form) },
      };
      result = editor.goal
        ? await execute(
            "goals.edit",
            {
              ...params,
              id: editor.goal.id,
              expected_revision: editor.goal.revision,
            },
            "Goal updated.",
          )
        : await execute(
            "goals.create",
            params,
            "Goal created. Activate it when you are ready.",
          );
    } else if (editor.kind === "commitment") {
      result = await execute(
        "commitments.create",
        {
          description: String(form.get("description")),
          beneficiary: String(form.get("beneficiary")),
        },
        "Responsibility proposed. Accept it to make the commitment active.",
      );
    } else if (editor.kind === "schedule") {
      const at = new Date(String(form.get("at"))).getTime() / 1000;
      if (!Number.isFinite(at) || at <= Date.now() / 1000) {
        setError("Choose a future start time.");
        return;
      }
      const conversation_id = await conversation();
      if (!conversation_id) return;
      const spec =
        scheduleKind === "interval"
          ? {
              kind: "interval",
              every_seconds: Number(form.get("every")) * 60,
              anchor: at,
            }
          : { kind: "once", at };
      result = await execute(
        "schedules.create",
        {
          conversation_id,
          text: String(form.get("text")),
          spec,
          resources: resources(form),
        },
        "Scheduled work saved.",
      );
    } else {
      const descriptions = String(form.get("steps"))
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean);
      if (!descriptions.length || descriptions.length > 64) {
        setError("Add between one and 64 steps.");
        return;
      }
      const budget = resources(form);
      if (budget.tokens < descriptions.length) {
        setError("Allow at least one token for each step.");
        return;
      }
      if (budget.iterations < descriptions.length) {
        setError("Allow at least one model turn for each step.");
        return;
      }
      result = await execute(
        "workflows.create",
        {
          steps: descriptions.map((description, index) => ({
            id: "step_" + index,
            description,
            dependencies: index ? ["step_" + (index - 1)] : [],
            budget: Object.fromEntries(
              Object.entries(budget).map(([key, value]) => [
                key,
                Math.floor(value / descriptions.length),
              ]),
            ),
          })),
          budget,
        },
        "Workflow created. Each step waits for its dependencies.",
      );
    }
    if (result !== undefined) {
      setTab(
        {
          goal: "Goals",
          commitment: "Commitments",
          schedule: "Schedules",
          workflow: "Workflows",
        }[editor.kind],
      );
      setEditor(null);
    }
  }
  const newKind =
    tab === "Commitments"
      ? "commitment"
      : tab === "Schedules"
        ? "schedule"
        : tab === "Workflows"
          ? "workflow"
          : "goal";
  const createLabel =
    newKind === "goal"
      ? "New goal"
      : newKind === "commitment"
        ? "New commitment"
        : newKind === "schedule"
          ? "Schedule prompt"
          : "New workflow";
  const data = query.data;
  const goals = data?.goals.filter((goal) => !goal.archived) || [];
  const live = data?.work.filter((work) => activeWork.has(work.state)) || [];
  const upcoming =
    data?.schedules.filter((schedule) => schedule.state === "active") || [];
  let content: ReactNode;
  if (query.isPending)
    content = (
      <div className="stack">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  else if (query.error || !data)
    content = (
      <div className="page-error">
        <h2>Work could not be loaded</h2>
        <p>{query.error?.message}</p>
        <Button onClick={() => query.refetch()}>Try again</Button>
      </div>
    );
  else if (tab === "Goals")
    content = (
      <Card
        title="The outcomes that matter"
        description="Activate a goal deliberately. Completion remains tied to evidence."
      >
        {goals.length ? (
          goals.map((goal) => (
            <WorkRow
              key={goal.id}
              icon={<Target size={18} />}
              title={goal.objective}
              state={goal.state}
              actions={
                <>
                  {[
                    "proposed",
                    "paused",
                    "waiting",
                    "reviewing",
                    "blocked",
                  ].includes(goal.state) && (
                    <Button
                      size="sm"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "goals.transition",
                          {
                            id: goal.id,
                            state: "active",
                            expected_revision: goal.revision,
                          },
                          "Goal activated.",
                        )
                      }
                    >
                      <Play size={13} />
                      {goal.state === "proposed"
                        ? "Activate goal"
                        : "Resume goal"}
                    </Button>
                  )}
                  {goal.state === "active" && (
                    <Button
                      size="sm"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "goals.transition",
                          {
                            id: goal.id,
                            state: "paused",
                            expected_revision: goal.revision,
                          },
                          "Goal paused.",
                        )
                      }
                    >
                      <Pause size={13} />
                      Pause goal
                    </Button>
                  )}
                  {!terminalGoals.has(goal.state) && (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => open({ kind: "goal", goal })}
                      >
                        Edit goal
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={pending}
                        onClick={() =>
                          execute(
                            "goals.transition",
                            {
                              id: goal.id,
                              state: "abandoned",
                              expected_revision: goal.revision,
                              reason: "Owner ended this goal",
                            },
                            "Goal abandoned; future execution is fenced.",
                          )
                        }
                      >
                        Abandon goal
                      </Button>
                    </>
                  )}
                  {terminalGoals.has(goal.state) && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "goals.archive",
                          { id: goal.id, expected_revision: goal.revision },
                          "Goal archived.",
                        )
                      }
                    >
                      Archive goal
                    </Button>
                  )}
                </>
              }
            >
              <Evidence value={goal} />
            </WorkRow>
          ))
        ) : (
          <EmptyState
            icon={<Target />}
            title="Give your work a direction"
            description="A goal holds the outcome you care about, its resource allowance, and the evidence of progress."
            action={
              <Button onClick={() => open({ kind: "goal" })}>
                Create a goal
              </Button>
            }
          />
        )}
      </Card>
    );
  else if (tab === "Commitments")
    content = (
      <Card
        title="Accepted responsibilities"
        description="A proposed responsibility becomes a commitment when you accept it."
      >
        {data.commitments.length ? (
          data.commitments.map((commitment) => (
            <WorkRow
              key={commitment.id}
              icon={<Flag size={18} />}
              title={commitment.description}
              state={commitment.state}
              subtitle={`${titleCase(commitment.progress)} · For ${commitment.beneficiary}`}
              actions={
                <>
                  {commitment.state === "proposed" && (
                    <Button
                      size="sm"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "commitments.transition",
                          {
                            id: commitment.id,
                            state: "accepted",
                            expected_revision: commitment.revision,
                          },
                          "Commitment accepted.",
                        )
                      }
                    >
                      <Check size={13} />
                      Accept commitment
                    </Button>
                  )}
                  {commitment.state === "accepted" && !commitment.wait_id && (
                    <Button
                      size="sm"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "commitments.progress",
                          {
                            id: commitment.id,
                            progress:
                              commitment.progress === "blocked"
                                ? "in_progress"
                                : "blocked",
                            expected_revision: commitment.revision,
                            reason: "Owner updated responsibility progress",
                          },
                          "Progress updated.",
                        )
                      }
                    >
                      {commitment.progress === "blocked"
                        ? "Mark in progress"
                        : "Mark blocked"}
                    </Button>
                  )}
                  {["proposed", "accepted"].includes(commitment.state) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "commitments.transition",
                          {
                            id: commitment.id,
                            state: "cancelled",
                            expected_revision: commitment.revision,
                            reason: "Owner released this responsibility",
                          },
                          "Commitment cancelled.",
                        )
                      }
                    >
                      Cancel commitment
                    </Button>
                  )}
                </>
              }
            >
              <Evidence value={commitment} />
            </WorkRow>
          ))
        ) : (
          <EmptyState
            icon={<Flag />}
            title="Clear promises. Visible progress."
            description="Keep responsibilities explicit, including who they are for and what is still waiting."
          />
        )}
      </Card>
    );
  else if (tab === "Schedules")
    content = (
      <Card
        title="A place in time"
        description="Scheduled prompts return to their conversation with their own compute allowance."
      >
        {data.schedules.length ? (
          data.schedules.map((schedule) => (
            <WorkRow
              key={schedule.id}
              icon={<CalendarClock size={18} />}
              title={schedule.payload.text || "Scheduled prompt"}
              state={schedule.state}
              subtitle={
                schedule.next_due
                  ? "Next run · " +
                    new Date(schedule.next_due * 1000).toLocaleString()
                  : "No upcoming run"
              }
              actions={
                <>
                  {["active", "paused"].includes(schedule.state) && (
                    <>
                      <Button
                        size="sm"
                        disabled={pending}
                        onClick={() =>
                          execute(
                            "schedules.state",
                            {
                              id: schedule.id,
                              state:
                                schedule.state === "active"
                                  ? "paused"
                                  : "active",
                              expected_revision: schedule.revision,
                            },
                            "Schedule updated.",
                          )
                        }
                      >
                        {schedule.state === "active" ? (
                          <Pause size={13} />
                        ) : (
                          <Play size={13} />
                        )}
                        {schedule.state === "active"
                          ? "Pause schedule"
                          : "Resume schedule"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={pending}
                        onClick={() =>
                          execute(
                            "schedules.state",
                            {
                              id: schedule.id,
                              state: "cancelled",
                              expected_revision: schedule.revision,
                            },
                            "Schedule cancelled.",
                          )
                        }
                      >
                        Cancel schedule
                      </Button>
                    </>
                  )}
                </>
              }
            >
              <Evidence value={schedule} />
            </WorkRow>
          ))
        ) : (
          <EmptyState
            icon={<CalendarClock />}
            title="Let the right work find its moment"
            description="Schedule a one-time prompt or a repeating check-in. You decide when and how much it may use."
            action={
              <Button onClick={() => open({ kind: "schedule" })}>
                Schedule prompt
              </Button>
            }
          />
        )}
      </Card>
    );
  else if (tab === "Workflows")
    content = (
      <Card
        title="One step makes the next possible"
        description="Dependencies run through the agent’s scheduler. Each result retains its evidence."
      >
        {data.workflows.length ? (
          data.workflows.map((plan) => (
            <WorkRow
              key={plan.id}
              icon={<GitBranch size={18} />}
              title={plan.steps[0]?.description || "Workflow"}
              state={plan.state}
              subtitle={`${plan.steps.filter((step) => step.state === "completed").length} of ${plan.steps.length} steps complete`}
              actions={
                <>
                  {plan.state === "active" && (
                    <>
                      <Button
                        size="sm"
                        disabled={pending}
                        onClick={() => runWorkflow(plan)}
                      >
                        <Play size={13} />
                        Run workflow
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={pending}
                        onClick={() =>
                          execute(
                            "workflows.state",
                            {
                              id: plan.id,
                              state: "paused",
                              expected_revision: plan.revision,
                              reason: "Owner paused workflow",
                            },
                            "Workflow paused.",
                          )
                        }
                      >
                        Pause workflow
                      </Button>
                    </>
                  )}
                  {plan.state === "paused" && (
                    <Button
                      size="sm"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "workflows.state",
                          {
                            id: plan.id,
                            state: "active",
                            expected_revision: plan.revision,
                          },
                          "Workflow resumed. Choose Run to continue.",
                        )
                      }
                    >
                      <Play size={13} />
                      Resume workflow
                    </Button>
                  )}
                </>
              }
            >
              <ol className="wc-steps">
                {plan.steps.map((step) => (
                  <li key={step.id}>
                    <span
                      className={
                        step.state === "completed" ? "wc-step-done" : ""
                      }
                    >
                      {step.state === "completed" ? (
                        <Check size={12} />
                      ) : (
                        <Circle size={9} />
                      )}
                    </span>
                    <span>{step.description}</span>
                    <small>{titleCase(step.state)}</small>
                  </li>
                ))}
              </ol>
              <Evidence value={plan} />
            </WorkRow>
          ))
        ) : (
          <EmptyState
            icon={<GitBranch />}
            title="Build a little momentum"
            description="Turn a larger task into dependent steps. Narrative completion and observed world outcomes remain distinct."
            action={
              <Button onClick={() => open({ kind: "workflow" })}>
                New workflow
              </Button>
            }
          />
        )}
      </Card>
    );
  else if (tab === "Inbox")
    content = (
      <div className="page-grid">
        <Card
          title="Proposals for your attention"
          description="Suggestions become work when you accept them."
        >
          {data.inbox.length ? (
            data.inbox.map((proposal) => (
              <WorkRow
                key={proposal.id}
                icon={<Inbox size={18} />}
                title={
                  typeof proposal.candidate.action === "string"
                    ? proposal.candidate.action
                    : proposal.candidate.action.text || "Initiative proposal"
                }
                state={proposal.decision}
                actions={
                  proposal.decision === "pending" && (
                    <>
                      <Button
                        size="sm"
                        disabled={pending}
                        onClick={async () => {
                          const conversation_id = await conversation();
                          if (conversation_id)
                            await execute(
                              "inbox.decide",
                              {
                                id: proposal.id,
                                decision: "accept",
                                conversation_id,
                                resources: {
                                  tokens: 64000,
                                  iterations: 8,
                                  elapsed_ms: 120000,
                                  tool_calls: 8,
                                },
                              },
                              "Proposal accepted with a bounded allowance.",
                            );
                        }}
                      >
                        Accept proposal
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={pending}
                        onClick={() =>
                          execute(
                            "inbox.decide",
                            { id: proposal.id, decision: "dismiss" },
                            "Proposal dismissed.",
                          )
                        }
                      >
                        Dismiss proposal
                      </Button>
                    </>
                  )
                }
              >
                <Evidence value={proposal} />
              </WorkRow>
            ))
          ) : (
            <EmptyState
              icon={<Inbox />}
              title="Nothing needs your attention"
              description="Relevant proposals will arrive here when the agent has a reason to bring them to you."
            />
          )}
        </Card>
        <Card
          title="Delegated work"
          description="Child results retain their own scope and acceptance evidence."
        >
          {data.children.length ? (
            data.children.map((child) => (
              <WorkRow
                key={child.id}
                icon={<GitBranch size={18} />}
                title={child.objective || child.kind || "Delegated task"}
                state={child.state}
                actions={
                  child.state === "active" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "children.cancel",
                          { id: child.id },
                          "Delegated work cancelled.",
                        )
                      }
                    >
                      Cancel delegated work
                    </Button>
                  )
                }
              >
                <Evidence value={child} />
              </WorkRow>
            ))
          ) : (
            <EmptyState
              icon={<GitBranch />}
              title="No delegated work"
              description="When your agent delegates a bounded task, its progress and returned evidence appear here."
            />
          )}
        </Card>
      </div>
    );
  else
    content = (
      <>
        <div className="wc-metrics">
          <div>
            <span className="wc-metric-icon">
              <Target size={18} />
            </span>
            <div>
              <strong>
                {goals.filter((goal) => goal.state === "active").length}
              </strong>
              <span>Active goals</span>
            </div>
          </div>
          <div>
            <span className="wc-metric-icon">
              <CalendarClock size={18} />
            </span>
            <div>
              <strong>{upcoming.length}</strong>
              <span>Upcoming schedules</span>
            </div>
          </div>
          <div>
            <span className="wc-metric-icon">
              <Layers2 size={18} />
            </span>
            <div>
              <strong>{live.length}</strong>
              <span>Work in motion</span>
            </div>
          </div>
        </div>
        <Card
          title="Agent activity"
          description="Recent work and its recorded outcomes."
        >
          {data.work.length ? (
            data.work.map((work) => (
              <WorkRow
                key={work.id}
                icon={
                  work.state === "running" ? (
                    <Play size={17} />
                  ) : (
                    <Clock3 size={17} />
                  )
                }
                title={work.objective || titleCase(work.kind)}
                state={work.state}
                actions={
                  activeWork.has(work.state) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={pending}
                      onClick={() =>
                        execute(
                          "work.cancel",
                          { id: work.id },
                          "New execution fenced; cancellation requested.",
                        )
                      }
                    >
                      <X size={13} />
                      Cancel work
                    </Button>
                  )
                }
              >
                <Evidence value={work} />
              </WorkRow>
            ))
          ) : (
            <EmptyState
              icon={<Layers2 />}
              title="Room for what comes next"
              description="Start with an outcome, a responsibility, or a scheduled prompt. Your agent’s activity will stay visible here."
              action={
                <Button
                  onClick={() => {
                    setTab("Goals");
                    open({ kind: "goal" });
                  }}
                >
                  Create a goal
                  <ArrowRight size={14} />
                </Button>
              }
            />
          )}
        </Card>
      </>
    );
  return (
    <div className="page wc-page">
      <header className="page-heading">
        <div>
          <div className="eyebrow">Keep things moving</div>
          <h1>Work, with continuity.</h1>
          <p>Your goals, commitments, and next steps. All in one place.</p>
        </div>
        <div className="row">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Refresh work"
            onClick={() => query.refetch()}
          >
            <RefreshCw size={16} />
          </Button>
          <Button variant="primary" onClick={() => open({ kind: newKind })}>
            <Plus size={15} />
            {createLabel}
          </Button>
        </div>
      </header>
      <Tabs value={tab} onValueChange={setTab} items={tabs}>
        <div className="wc-tab-content">{content}</div>
      </Tabs>
      <Modal
        open={editor !== null}
        onOpenChange={(value) => {
          if (!value) setEditor(null);
        }}
        title={
          editor?.kind === "goal"
            ? editor.goal
              ? "Edit goal"
              : "New goal"
            : editor?.kind === "commitment"
              ? "New commitment"
              : editor?.kind === "schedule"
                ? "Schedule prompt"
                : "New workflow"
        }
        description={
          editor?.kind === "workflow"
            ? "Write one step per line. Each step waits for the previous one."
            : editor?.kind === "schedule"
              ? "Choose a conversation, a start time, and a bounded allowance."
              : "Make the intention clear, then choose when to put it in motion."
        }
      >
        {editor && (
          <form
            key={editor.kind + (editor.goal?.id || "")}
            className="stack"
            onSubmit={submit}
          >
            {editor.kind === "goal" && (
              <>
                <label className="field">
                  Desired outcome
                  <Textarea
                    name="objective"
                    defaultValue={editor.goal?.objective}
                    placeholder="What would you like to make happen?"
                    required
                    maxLength={65536}
                  />
                </label>
                <Budget
                  tokens={Number(editor.goal?.limits?.tokens ?? 64000)}
                  iterations={Number(editor.goal?.limits?.iterations ?? 8)}
                  seconds={
                    Number(editor.goal?.limits?.elapsed_ms ?? 120000) / 1000
                  }
                  calls={Number(editor.goal?.limits?.tool_calls ?? 8)}
                />
              </>
            )}
            {editor.kind === "commitment" && (
              <>
                <label className="field">
                  Responsibility
                  <Textarea
                    name="description"
                    placeholder="What needs to be followed through?"
                    required
                  />
                </label>
                <label className="field">
                  Who is it for?
                  <Input name="beneficiary" defaultValue="owner" required />
                </label>
              </>
            )}
            {editor.kind === "schedule" && (
              <>
                <label className="field">
                  Prompt
                  <Textarea
                    name="text"
                    placeholder="What should your agent pick up?"
                    required
                  />
                </label>
                <Select
                  label="Conversation"
                  value={conversationId}
                  onValueChange={setConversationId}
                  items={[
                    {
                      value: "automatic",
                      label:
                        rooms.data?.items[0]?.title ||
                        "Create a work conversation",
                    },
                    ...(rooms.data?.items || []).map((room) => ({
                      value: room.id,
                      label: room.title,
                    })),
                  ]}
                />
                <Select
                  label="Frequency"
                  value={scheduleKind}
                  onValueChange={setScheduleKind}
                  items={[
                    { value: "once", label: "Once" },
                    { value: "interval", label: "Repeating interval" },
                  ]}
                />
                <label className="field">
                  Start time
                  <Input name="at" type="datetime-local" required />
                </label>
                {scheduleKind === "interval" && (
                  <label className="field">
                    Repeat every (minutes)
                    <Input
                      name="every"
                      type="number"
                      defaultValue={60}
                      min={1}
                      step={1}
                      required
                    />
                  </label>
                )}
                <Budget />
              </>
            )}
            {editor.kind === "workflow" && (
              <>
                <label className="field">
                  Workflow steps
                  <Textarea
                    name="steps"
                    placeholder={
                      "Read the source material\nDraft the summary\nReview the result"
                    }
                    required
                    rows={5}
                  />
                </label>
                <Budget
                  tokens={200000}
                  iterations={16}
                  seconds={600}
                  calls={16}
                />
                <p className="field-hint">
                  This allowance is shared across all steps. A completed model
                  turn does not, on its own, prove an external outcome.
                </p>
              </>
            )}
            {error && (
              <p role="alert" className="wc-form-error">
                {error}
              </p>
            )}
            <div className="form-actions">
              <Button variant="ghost" onClick={() => setEditor(null)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" loading={pending}>
                {editor.kind === "goal"
                  ? editor.goal
                    ? "Save goal"
                    : "Create goal"
                  : editor.kind === "commitment"
                    ? "Create commitment"
                    : editor.kind === "schedule"
                      ? "Save schedule"
                      : "Create workflow"}
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}
