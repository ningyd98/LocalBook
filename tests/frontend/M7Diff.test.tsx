import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DiffView } from "../../apps/web/src/components/DiffView";
import { ConfirmDialog } from "../../apps/web/src/components/ConfirmDialog";
import type { DiffEntryDTO } from "../../packages/protocol/src";

const MALICIOUS_DIFF = '@@ -1 +1 @@\n-<img src=x onerror="globalThis.pwned=1">\n+clean\n';

const entries: DiffEntryDTO[] = [
  {
    path: "notes/a.md",
    action_id: "action-1",
    operation: "update",
    before_hash: "sha256:old",
    after_hash: "sha256:new",
    before_size: 10,
    after_size: 11,
    unified_diff: MALICIOUS_DIFF,
    status: "proposed",
  },
  {
    path: "notes/b.md",
    action_id: "action-2",
    operation: "create",
    before_hash: null,
    after_hash: "sha256:newb",
    before_size: 0,
    after_size: 6,
    unified_diff: null,
    status: "accepted",
  },
];

describe("DiffView", () => {
  it("renders diff text as inert text (no dangerouslySetInnerHTML execution)", async () => {
    render(<DiffView diff={entries} />);
    expect(document.querySelector("img")).not.toBeInTheDocument();
    const pre = document.querySelector("pre");
    expect(pre?.textContent).toContain("onerror");
    expect((globalThis as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it("shows both proposed and accepted entries with statuses", () => {
    render(<DiffView diff={entries} />);
    expect(screen.getByText("notes/a.md")).toBeInTheDocument();
    expect(screen.getByText("proposed")).toBeInTheDocument();
    expect(screen.getByText("accepted")).toBeInTheDocument();
    expect(screen.getByText(/create: 0 → 6 bytes/)).toBeInTheDocument();
  });

  it("shows per-entry Accept only for proposed entries", async () => {
    const onAccept = vi.fn();
    render(<DiffView diff={entries} onAccept={onAccept} />);
    expect(screen.getByRole("button", { name: "Accept update" })).toBeInTheDocument();
    // entry b is accepted -> no accept button for it
    expect(screen.queryByRole("button", { name: "Accept create" })).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Accept update" }));
    await waitFor(() => expect(onAccept).toHaveBeenCalledWith("action-1"));
  });

  it("renders an empty state without buttons", () => {
    render(<DiffView diff={[]} />);
    expect(screen.getByText("No changes proposed.")).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("is a labelled modal dialog and Escape cancels", async () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog open message="Accept these writes?" onCancel={onCancel} onConfirm={onConfirm} />,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("Confirm changes");
    const user = userEvent.setup();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(onCancel).toHaveBeenCalled());
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("does not render when closed", () => {
    render(<ConfirmDialog open={false} message="x" onCancel={() => undefined} onConfirm={() => undefined} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
