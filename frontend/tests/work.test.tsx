import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import WorkPage from "../src/pages/WorkPage";
import { mountPage } from "./harness";
const empty = () => ({
  goals: [],
  commitments: [],
  schedules: [],
  workflows: [],
  work: [],
  children: [],
  inbox: [],
});
const selectTab = async (name: string) => {
  await userEvent.click(screen.getByRole("tab", { name, exact: true }));
};

it("creates bounded goals and uses the displayed revision for explicit activation and edits", async () => {
  const goal = {
    id: "goal",
    revision: 4,
    state: "proposed",
    objective: "Follow the sources",
    limits: { tokens: 32000, iterations: 4, elapsed_ms: 30000, tool_calls: 2 },
  };
  const page = mountPage(<WorkPage />, {
    "work.overview": () => ({ ...empty(), goals: [goal] }),
    "conversations.list": () => ({ items: [] }),
    "goals.transition": (params) => {
      expect(params).toEqual({
        id: "goal",
        state: "active",
        expected_revision: 4,
      });
      goal.state = "active";
      goal.revision++;
      return goal;
    },
    "goals.edit": (params) => params,
    "goals.create": (params) => params,
  });
  await screen.findByText("Room for what comes next");
  await selectTab("Goals");
  await userEvent.click(screen.getByRole("button", { name: "Activate goal" }));
  await screen.findByRole("button", { name: "Pause goal" });
  await userEvent.click(screen.getByRole("button", { name: "Edit goal" }));
  const dialog = screen.getByRole("dialog", { name: "Edit goal" });
  expect(
    (within(dialog).getByLabelText("Desired outcome") as HTMLTextAreaElement)
      .value,
  ).toBe("Follow the sources");
  await userEvent.clear(within(dialog).getByLabelText("Desired outcome"));
  await userEvent.type(
    within(dialog).getByLabelText("Desired outcome"),
    "Cross-check every source",
  );
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Save goal" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "goals.edit")?.params,
  ).toEqual({
    id: "goal",
    expected_revision: 5,
    objective: "Cross-check every source",
    limits: goal.limits,
  });
  await userEvent.click(screen.getByRole("button", { name: "New goal" }));
  await userEvent.type(
    screen.getByLabelText("Desired outcome"),
    "Prepare a review",
  );
  await userEvent.click(screen.getByRole("button", { name: "Create goal" }));
  await waitFor(() =>
    expect(page.calls.some((call) => call.method === "goals.create")).toBe(
      true,
    ),
  );
  expect(
    page.calls.find((call) => call.method === "goals.create")?.params.limits,
  ).toEqual({
    tokens: 64000,
    iterations: 8,
    elapsed_ms: 120000,
    tool_calls: 8,
  });
});
it("validates workflow length and compute allowance before creating dependent steps", async () => {
  const page = mountPage(<WorkPage />, {
    "work.overview": empty,
    "conversations.list": () => ({ items: [] }),
    "workflows.create": (params) => params,
  });
  await screen.findByText("Room for what comes next");
  await selectTab("Workflows");
  await userEvent.click(
    screen.getAllByRole("button", { name: "New workflow" })[0],
  );
  const steps = screen.getByLabelText("Workflow steps");
  fireEvent.change(steps, { target: { value: " \n " } });
  await userEvent.click(
    screen.getByRole("button", { name: "Create workflow" }),
  );
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Add between one and 64 steps.",
  );
  fireEvent.change(steps, { target: { value: "Read evidence\nWrite review" } });
  fireEvent.change(screen.getByLabelText("Model turns"), {
    target: { value: "1" },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Create workflow" }),
  );
  expect(screen.getByRole("alert").textContent).toBe(
    "Allow at least one model turn for each step.",
  );
  expect(page.calls.some((call) => call.method === "workflows.create")).toBe(
    false,
  );
  fireEvent.change(screen.getByLabelText("Model turns"), {
    target: { value: "16" },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Create workflow" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "workflows.create")?.params.steps,
  ).toEqual([
    {
      id: "step_0",
      description: "Read evidence",
      dependencies: [],
      budget: {
        tokens: 100000,
        iterations: 8,
        elapsed_ms: 300000,
        tool_calls: 8,
      },
    },
    {
      id: "step_1",
      description: "Write review",
      dependencies: ["step_0"],
      budget: {
        tokens: 100000,
        iterations: 8,
        elapsed_ms: 300000,
        tool_calls: 8,
      },
    },
  ]);
});
it("schedules reject past dates and bind repeating work to a selected conversation", async () => {
  const page = mountPage(<WorkPage />, {
    "work.overview": empty,
    "conversations.list": () => ({ items: [{ id: "room", title: "Review" }] }),
    "schedules.create": (params) => params,
  });
  await screen.findByText("Room for what comes next");
  await selectTab("Schedules");
  await userEvent.click(
    screen.getAllByRole("button", { name: "Schedule prompt" })[0],
  );
  await userEvent.type(
    screen.getByLabelText("Prompt"),
    "Read the overnight sources",
  );
  fireEvent.change(screen.getByLabelText("Start time"), {
    target: { value: "2000-01-01T12:00" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Choose a future start time.",
  );
  fireEvent.change(screen.getByLabelText("Start time"), {
    target: { value: "2035-01-01T12:00" },
  });
  screen.getByRole("combobox", { name: "Frequency" }).focus();
  await userEvent.keyboard("{Enter}{End}{Enter}");
  await screen.findByLabelText("Repeat every (minutes)");
  fireEvent.change(screen.getByLabelText("Repeat every (minutes)"), {
    target: { value: "30" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "schedules.create")?.params,
  ).toMatchObject({
    conversation_id: "room",
    text: "Read the overnight sources",
    spec: { kind: "interval", every_seconds: 1800 },
  });
});
it("proposed responsibilities require explicit acceptance and committed progress remains separate", async () => {
  const row = {
    id: "promise",
    revision: 1,
    state: "proposed",
    description: "Review evidence",
    beneficiary: "owner",
    progress: "not_started",
    wait_id: null,
  };
  const page = mountPage(<WorkPage />, {
    "work.overview": () => ({ ...empty(), commitments: [row] }),
    "conversations.list": () => ({ items: [] }),
    "commitments.transition": (params) => {
      row.state = String(params.state);
      row.revision++;
      return row;
    },
    "commitments.progress": (params) => {
      row.progress = String(params.progress);
      row.revision++;
      return row;
    },
    "commitments.create": (params) => params,
  });
  await screen.findByText("Room for what comes next");
  await selectTab("Commitments");
  expect(screen.queryByRole("button", { name: "Mark blocked" })).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Accept commitment" }),
  );
  await screen.findByRole("button", { name: "Mark blocked" });
  expect(
    page.calls.find((call) => call.method === "commitments.transition")?.params,
  ).toEqual({ id: "promise", state: "accepted", expected_revision: 1 });
  await userEvent.click(screen.getByRole("button", { name: "Mark blocked" }));
  await screen.findByRole("button", { name: "Mark in progress" });
  await userEvent.click(
    screen.getByRole("button", { name: "Mark in progress" }),
  );
  await screen.findByRole("button", { name: "Mark blocked" });
  await userEvent.click(screen.getByRole("button", { name: "New commitment" }));
  await userEvent.type(
    screen.getByLabelText("Responsibility"),
    "Check the result",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Create commitment" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "commitments.create")?.params,
  ).toEqual({ description: "Check the result", beneficiary: "owner" });
});

it("goal and schedule lifecycle actions carry fresh revisions and terminal rows cannot resume", async () => {
  const goal = {
    id: "goal",
    revision: 4,
    state: "active",
    objective: "Publish a review",
    limits: { tokens: 32000, iterations: 4, elapsed_ms: 30000, tool_calls: 2 },
  };
  const schedule = {
    id: "timer",
    revision: 7,
    state: "active",
    next_due: 2050000000,
    payload: { text: "Review the inbox" },
  };
  const commitment = {
    id: "promise",
    revision: 2,
    state: "accepted",
    description: "Check deliveries",
    beneficiary: "owner",
    progress: "in_progress",
    wait_id: null,
  };
  let goals = [goal];
  const page = mountPage(<WorkPage />, {
    "work.overview": () => ({
      ...empty(),
      goals,
      schedules: [schedule],
      commitments: [commitment],
    }),
    "conversations.list": () => ({ items: [] }),
    "goals.transition": (params) => {
      expect(params.expected_revision).toBe(goal.revision);
      goal.state = String(params.state);
      goal.revision++;
      return goal;
    },
    "goals.archive": (params) => {
      expect(params).toEqual({ id: "goal", expected_revision: 7 });
      goals = [];
      return { archived: true };
    },
    "schedules.state": (params) => {
      expect(params.expected_revision).toBe(schedule.revision);
      schedule.state = String(params.state);
      schedule.revision++;
      return schedule;
    },
    "commitments.transition": (params) => {
      expect(params).toEqual({
        id: "promise",
        state: "cancelled",
        expected_revision: 2,
        reason: "Owner released this responsibility",
      });
      commitment.state = "cancelled";
      return commitment;
    },
  });
  await screen.findByText("Room for what comes next");
  await selectTab("Goals");
  await userEvent.click(screen.getByRole("button", { name: "Pause goal" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Resume goal" }),
  );
  await screen.findByRole("button", { name: "Pause goal" });
  await userEvent.click(screen.getByRole("button", { name: "Abandon goal" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Archive goal" }),
  );
  await screen.findByText("Give your work a direction");
  expect(
    page.calls
      .filter((call) => call.method === "goals.transition")
      .map((call) => call.params.state),
  ).toEqual(["paused", "active", "abandoned"]);
  await selectTab("Schedules");
  await userEvent.click(screen.getByRole("button", { name: "Pause schedule" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Resume schedule" }),
  );
  await screen.findByRole("button", { name: "Pause schedule" });
  await userEvent.click(
    screen.getByRole("button", { name: "Cancel schedule" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Resume schedule" }),
    ).toBeNull(),
  );
  await screen.findByText("Cancelled");
  expect(
    page.calls
      .filter((call) => call.method === "schedules.state")
      .map((call) => call.params.expected_revision),
  ).toEqual([7, 8, 9]);
  await selectTab("Commitments");
  await userEvent.click(
    screen.getByRole("button", { name: "Cancel commitment" }),
  );
  await screen.findByText("Cancelled");
  expect(screen.queryByRole("button", { name: "Mark blocked" })).toBeNull();
});

it("workflow runs create a conversation when needed and preserve dependency progress across pause and resume", async () => {
  const plan = {
    id: "plan",
    revision: 3,
    state: "active",
    steps: [
      {
        id: "read",
        description: "Read source evidence",
        state: "completed",
        dependencies: [],
      },
      {
        id: "write",
        description: "Write the review",
        state: "pending",
        dependencies: ["read"],
      },
    ],
    execution: {},
  };
  const rooms: Array<{ id: string; title: string }> = [];
  const page = mountPage(<WorkPage />, {
    "work.overview": () => ({ ...empty(), workflows: [plan] }),
    "conversations.list": () => ({ items: rooms }),
    "conversations.create": (params) => {
      expect(params).toEqual({ title: "Ongoing work" });
      const room = { id: "new-room", title: "Ongoing work" };
      rooms.push(room);
      return room;
    },
    "workflows.run": (params) => {
      expect(params).toEqual({
        id: "plan",
        conversation_id: "new-room",
        expected_revision: plan.revision,
      });
      plan.steps[1].state = "completed";
      plan.execution = { evidence_event_ids: ["effect-observed"] };
      plan.revision++;
      return plan;
    },
    "workflows.state": (params) => {
      expect(params.expected_revision).toBe(plan.revision);
      plan.state = String(params.state);
      plan.revision++;
      return plan;
    },
  });
  await screen.findByText("Room for what comes next");
  await selectTab("Workflows");
  await screen.findByText("1 of 2 steps complete");
  await userEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await screen.findByText("2 of 2 steps complete");
  expect(
    page.calls
      .filter((call) =>
        ["conversations.create", "workflows.run"].includes(call.method),
      )
      .map((call) => call.method),
  ).toEqual(["conversations.create", "workflows.run"]);
  await userEvent.click(screen.getByText("Inspect evidence"));
  expect(screen.getByText(/"effect-observed"/)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Pause workflow" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Resume workflow" }),
  );
  await screen.findByRole("button", { name: "Run workflow" });
  expect(
    page.calls
      .filter((call) => call.method === "workflows.state")
      .map((call) => call.params),
  ).toEqual([
    {
      id: "plan",
      state: "paused",
      expected_revision: 4,
      reason: "Owner paused workflow",
    },
    { id: "plan", state: "active", expected_revision: 5 },
  ]);
});

it("inbox decisions create bounded work while child and parent cancellation retain their evidence", async () => {
  const inbox = [
    {
      id: "yes",
      decision: "pending",
      candidate: { id: "candidate-a", action: "Review new source" },
    },
    {
      id: "no",
      decision: "pending",
      candidate: { id: "candidate-b", action: { text: "Recheck old source" } },
    },
  ];
  const child = {
    id: "child",
    state: "active",
    kind: "memory_scout",
    objective: "Locate source citations",
    result: { evidence_event_ids: ["source-17"] },
  };
  const work = {
    id: "work",
    state: "running",
    kind: "model",
    objective: "Draft report",
  };
  const page = mountPage(<WorkPage />, {
    "work.overview": () => ({
      ...empty(),
      inbox,
      children: [child],
      work: [work],
    }),
    "conversations.list": () => ({
      items: [{ id: "room", title: "Daily review" }],
    }),
    "inbox.decide": (params) => {
      const row = inbox.find((item) => item.id === params.id)!;
      row.decision = String(params.decision);
      return row;
    },
    "children.cancel": (params) => {
      expect(params).toEqual({ id: "child" });
      child.state = "cancelled";
      return child;
    },
    "work.cancel": (params) => {
      expect(params).toEqual({ id: "work" });
      work.state = "cancelled";
      return work;
    },
  });
  await screen.findByText("Draft report");
  await userEvent.click(screen.getByRole("button", { name: "Cancel work" }));
  await screen.findByText("Cancelled");
  await selectTab("Inbox");
  const accept = screen
    .getByRole("heading", { name: "Review new source" })
    .closest("article")!;
  await userEvent.click(
    within(accept).getByRole("button", { name: "Accept proposal" }),
  );
  await waitFor(() =>
    expect(
      within(accept).queryByRole("button", { name: "Accept proposal" }),
    ).toBeNull(),
  );
  const reject = screen
    .getByRole("heading", { name: "Recheck old source" })
    .closest("article")!;
  await userEvent.click(
    within(reject).getByRole("button", { name: "Dismiss proposal" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Cancel delegated work" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Cancel delegated work" }),
    ).toBeNull(),
  );
  const delegated = screen
    .getByRole("heading", { name: "Locate source citations" })
    .closest("article")!;
  await userEvent.click(within(delegated).getByText("Inspect evidence"));
  expect(within(delegated).getByText(/"source-17"/)).toBeTruthy();
  expect(
    page.calls
      .filter((call) => call.method === "inbox.decide")
      .map((call) => call.params),
  ).toEqual([
    {
      id: "yes",
      decision: "accept",
      conversation_id: "room",
      resources: {
        tokens: 64000,
        iterations: 8,
        elapsed_ms: 120000,
        tool_calls: 8,
      },
    },
    { id: "no", decision: "dismiss" },
  ]);
});

it("failed work reads retry without losing the bounded workflow editor and token validation prevents dispatch", async () => {
  let failed = true;
  const page = mountPage(<WorkPage />, {
    "work.overview": () => {
      if (failed) throw new Error("Work observation unavailable");
      return empty();
    },
    "conversations.list": () => ({ items: [] }),
  });
  await screen.findByText("Work could not be loaded");
  failed = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByText("Room for what comes next");
  await userEvent.click(screen.getByRole("button", { name: "Create a goal" }));
  await screen.findByRole("dialog", { name: "New goal" });
  await userEvent.keyboard("{Escape}");
  await selectTab("Workflows");
  await userEvent.click(
    screen.getAllByRole("button", { name: "New workflow" }).at(-1)!,
  );
  fireEvent.change(screen.getByLabelText("Workflow steps"), {
    target: { value: "Read source\nVerify source" },
  });
  fireEvent.change(screen.getByLabelText("Tokens"), { target: { value: "1" } });
  await userEvent.click(
    screen.getByRole("button", { name: "Create workflow" }),
  );
  expect(screen.getByRole("alert").textContent).toBe(
    "Allow at least one token for each step.",
  );
  expect(page.calls.some((call) => call.method === "workflows.create")).toBe(
    false,
  );
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
