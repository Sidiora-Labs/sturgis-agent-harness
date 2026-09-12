import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
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
  Tooltip,
  cx,
} from "../src/components/ui";
import { Shell, type ShellProps } from "../src/components/Shell";

it("buttons block repeated submission while pending and preserve form semantics", async () => {
  const click = vi.fn();
  const { rerender } = render(
    <Button loading onClick={click}>
      Save
    </Button>,
  );
  const button = screen.getByRole("button", { name: "Save" });
  expect(button.hasAttribute("disabled")).toBe(true);
  expect(button.getAttribute("aria-busy")).toBe("true");
  await userEvent.click(button);
  expect(click).not.toHaveBeenCalled();
  rerender(
    <Button type="submit" variant="primary" size="sm" onClick={click}>
      Save
    </Button>,
  );
  await userEvent.click(button);
  expect(click).toHaveBeenCalledOnce();
  expect(button.getAttribute("type")).toBe("submit");
  expect(cx("a", false, null, undefined, "b")).toBe("a b");
});
it("shared form, loading and empty states expose usable names without raw markup", () => {
  render(
    <>
      <Card
        title="Evidence"
        description="Read the source"
        actions={<Button>Refresh</Button>}
      >
        <Input aria-label="Title" />
        <Textarea aria-label="Body" />
        <Badge tone="success">Ready</Badge>
        <Badge>Idle</Badge>
        <Skeleton data-testid="loading" />
      </Card>
      <Card>Plain content</Card>
      <EmptyState
        title="No sources"
        description="Add one"
        action={<Button>Add source</Button>}
      />
      <EmptyState
        icon={<span>custom</span>}
        title="No work"
        description="Resting"
      />
    </>,
  );
  expect(screen.getByRole("heading", { name: "Evidence" })).toBeTruthy();
  expect(screen.getByLabelText("Title").tagName).toBe("INPUT");
  expect(screen.getByLabelText("Body").tagName).toBe("TEXTAREA");
  expect(screen.getByTestId("loading").getAttribute("aria-hidden")).toBe(
    "true",
  );
  expect(screen.getByRole("button", { name: "Add source" })).toBeTruthy();
});
it("controlled modal traps keyboard focus, closes with Escape and restores its opener", async () => {
  function Example() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button onClick={() => setOpen(true)}>Edit title</Button>
        <Modal
          open={open}
          onOpenChange={setOpen}
          title="Rename conversation"
          description="A title you can find later"
        >
          <Input aria-label="New title" />
          <Button onClick={() => setOpen(false)}>Save title</Button>
        </Modal>
      </>
    );
  }
  render(<Example />);
  const user = userEvent.setup();
  const opener = screen.getByRole("button", { name: "Edit title" });
  await user.click(opener);
  const dialog = screen.getByRole("dialog", { name: "Rename conversation" });
  expect(dialog.contains(document.activeElement)).toBe(true);
  expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
  await user.tab();
  await user.tab();
  await user.tab();
  expect(dialog.contains(document.activeElement)).toBe(true);
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(opener);
  await user.click(opener);
  await user.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("tabs and select support keyboard operation with controlled values", async () => {
  function Example() {
    const [tab, setTab] = useState("one"),
      [choice, setChoice] = useState("a");
    return (
      <>
        <Tabs
          value={tab}
          onValueChange={setTab}
          items={[
            { value: "one", label: "First" },
            { value: "two", label: "Second" },
          ]}
        >
          {tab} content
        </Tabs>
        <Select
          label="Workspace"
          value={choice}
          onValueChange={setChoice}
          items={[
            { value: "a", label: "Alpha" },
            { value: "b", label: "Beta" },
          ]}
        />
      </>
    );
  }
  render(<Example />);
  const user = userEvent.setup();
  screen.getByRole("tab", { name: "First" }).focus();
  await user.keyboard("{ArrowRight}");
  await waitFor(() =>
    expect(
      screen.getByRole("tab", { name: "Second" }).getAttribute("aria-selected"),
    ).toBe("true"),
  );
  const select = screen.getByRole("combobox", { name: "Workspace" });
  select.focus();
  await user.keyboard("{Enter}");
  await screen.findByRole("option", { name: "Beta" });
  await user.keyboard("{End}{Enter}");
  await waitFor(() => expect(select.textContent).toBe("Beta"));
});
it("tooltip labels appear for keyboard focus", async () => {
  render(
    <Tooltip label="Read activity">
      <Button aria-label="Activity">A</Button>
    </Tooltip>,
  );
  screen.getByRole("button", { name: "Activity" }).focus();
  expect(await screen.findByRole("tooltip")).toBeTruthy();
});
function shell(overrides: Partial<ShellProps> = {}) {
  return {
    children: <h1>Conversation content</h1>,
    view: "Conversation" as const,
    onNavigate: vi.fn(),
    agents: [
      { id: "one", name: "Research" },
      { id: "two", name: "Personal" },
    ],
    agentId: "one",
    onAgentChange: vi.fn(),
    onNewAgent: vi.fn(),
    user: { email: "owner@example.com" },
    onSignOut: vi.fn(),
    mode: "self_hosted" as const,
    connection: "live" as const,
    onCommandPalette: vi.fn(),
    ...overrides,
  };
}
it("shell controls navigate, select/create agents, open commands and sign out through callbacks", async () => {
  const props = shell();
  const { rerender } = render(<Shell {...props} />);
  const user = userEvent.setup();
  expect(screen.getByRole("status").textContent).toBe("Connected");
  await user.click(screen.getByRole("button", { name: "Work", exact: true }));
  expect(props.onNavigate).toHaveBeenCalledWith("Work");
  await user.click(
    screen.getByRole("button", { name: "Open command palette" }),
  );
  expect(props.onCommandPalette).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Switch agent" }));
  await user.click(screen.getByRole("menuitem", { name: "Personal" }));
  expect(props.onAgentChange).toHaveBeenCalledWith("two");
  await user.click(screen.getByRole("button", { name: "Switch agent" }));
  await user.click(screen.getByRole("menuitem", { name: "Create an agent" }));
  expect(props.onNewAgent).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Account menu" }));
  await user.click(screen.getByRole("menuitem", { name: "Settings" }));
  expect(props.onNavigate).toHaveBeenCalledWith("Settings");
  await user.click(screen.getByRole("button", { name: "Account menu" }));
  await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(props.onSignOut).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
  rerender(
    <Shell
      {...props}
      mode="hosted"
      connection="reconnecting"
      agents={[]}
      agentId={null}
      onCommandPalette={undefined}
    />,
  );
  expect(screen.getByText("Private hosted runtime")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("Reconnecting");
  rerender(<Shell {...props} connection="idle" />);
  expect(screen.getByRole("status").textContent).toBe("Ready");
});
it("mobile navigation sheet closes after navigation and agent actions", async () => {
  const props = shell();
  render(<Shell {...props} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open navigation" }));
  let dialog = screen.getByRole("dialog", { name: "Workspace navigation" });
  await user.click(
    within(dialog).getByRole("button", { name: "Knowledge", exact: true }),
  );
  expect(props.onNavigate).toHaveBeenCalledWith("Knowledge");
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Open navigation" }));
  dialog = screen.getByRole("dialog");
  await user.click(
    within(dialog).getByRole("button", { name: "Switch agent" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "Create an agent" }));
  expect(props.onNewAgent).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Open navigation" }));
  dialog = screen.getByRole("dialog");
  await user.click(
    within(dialog).getByRole("button", { name: "Switch agent" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "Personal" }));
  expect(props.onAgentChange).toHaveBeenCalledWith("two");
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Open navigation" }));
  await user.click(screen.getByRole("button", { name: "Close navigation" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("recent conversations select the intended thread and new conversation closes the mobile sheet", async () => {
  const props = shell({
    conversations: [{ id: "recent", title: "Cedar research" }],
    conversationId: "recent",
    onConversationSelect: vi.fn(),
    onNewConversation: vi.fn(),
  });
  const { rerender } = render(<Shell {...props} />);
  const user = userEvent.setup();
  const recent = within(
    screen.getByRole("navigation", { name: "Recent conversations" }),
  ).getByRole("button", { name: "Cedar research" });
  expect(recent.getAttribute("aria-current")).toBe("page");
  await user.click(recent);
  expect(props.onConversationSelect).toHaveBeenCalledWith("recent");
  await user.click(screen.getByRole("button", { name: "Open navigation" }));
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "New conversation",
    }),
  );
  expect(props.onNewConversation).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).toBeNull();
  rerender(<Shell {...props} conversations={[]} />);
  expect(screen.getByText("Your conversations will appear here.")).toBeTruthy();
});
