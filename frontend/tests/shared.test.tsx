import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import {
  Evidence,
  FormDialog,
  Metric,
  PageHeader,
  QueryState,
} from "../src/pages/shared";

it("query failures expose a retry action while loading and empty states stay distinct", async () => {
  const retry = vi.fn();
  const { rerender, container } = render(
    <QueryState error={null} loading retry={retry} />,
  );
  expect(container.querySelectorAll(".st-skeleton")).toHaveLength(2);
  rerender(
    <QueryState
      error={new Error("Runtime is waking")}
      loading={false}
      retry={retry}
    />,
  );
  expect(screen.getByText("Runtime is waking")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(retry).toHaveBeenCalledOnce();
  rerender(<QueryState error={null} loading={false} retry={retry} />);
  expect(container.textContent).toBe("");
});
it("domain forms preserve field types, defaults, required constraints and submit exact values", async () => {
  const submit = vi.fn().mockResolvedValue(undefined),
    close = vi.fn();
  render(
    <FormDialog
      open
      onOpenChange={close}
      title="Connection details"
      description="Choose the exact endpoint"
      submitLabel="Configure"
      fields={[
        {
          name: "name",
          label: "Name",
          value: "Research",
          required: true,
          hint: "Visible only to you",
        },
        {
          name: "notes",
          label: "Notes",
          type: "textarea",
          value: "Known source",
        },
        { name: "secret", label: "Secret", type: "password" },
        {
          name: "limit",
          label: "Limit",
          type: "number",
          min: 1,
          max: 10,
          value: 4,
        },
      ]}
      onSubmit={submit}
    >
      <span>Current revision 2</span>
    </FormDialog>,
  );
  const user = userEvent.setup();
  expect(screen.getByLabelText("Secret").getAttribute("autocomplete")).toBe(
    "new-password",
  );
  expect(screen.getByLabelText("Limit").getAttribute("max")).toBe("10");
  await user.clear(screen.getByLabelText(/Name/));
  await user.click(screen.getByRole("button", { name: "Configure" }));
  expect(submit).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText(/Name/), "Cedar");
  await user.type(screen.getByLabelText("Secret"), "private");
  await user.click(screen.getByRole("button", { name: "Configure" }));
  await waitFor(() =>
    expect(submit).toHaveBeenCalledWith(
      { name: "Cedar", notes: "Known source", secret: "private", limit: "4" },
      expect.any(HTMLFormElement),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(close).toHaveBeenCalledWith(false);
});
it("page summaries and explicit evidence display text without interpreting markup", () => {
  render(
    <>
      <PageHeader
        eyebrow="Workspace"
        title="Knowledge"
        description="Sources remain inspectable"
        actions={<button>Upload</button>}
      />
      <PageHeader title="Plain" description="No eyebrow" />
      <Metric label="Sources" value={3} footnote="Scoped to this agent" />
      <Metric label="Tasks" value={0} />
      <Evidence data={{ untrusted: "<script>run()</script>" }} />
    </>,
  );
  expect(screen.getByRole("heading", { name: "Knowledge" })).toBeTruthy();
  expect(screen.getByText("Scoped to this agent")).toBeTruthy();
  expect(screen.getByText(/"untrusted"/).tagName).toBe("PRE");
  expect(document.querySelector("script")).toBeNull();
});
