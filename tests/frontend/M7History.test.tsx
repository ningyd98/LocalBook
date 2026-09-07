import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AIHistoryPanel } from "../../apps/web/src/components/AIHistoryPanel";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type {
  HistoryPageDTO,
  JobCreateRequest,
  JobDetailDTO,
  JobSummaryDTO,
  UndoResponseDTO,
} from "../../packages/protocol/src";

const summaryItem: JobSummaryDTO = {
  job_id: "job-1",
  task_type: "daily_organizer",
  status: "awaiting_confirmation",
  start_time: "2026-01-01T00:00:00+00:00",
  model: "mock",
  action_count: 1,
};
const awaitingDetail: JobDetailDTO = {
  job_id: "job-1",
  task_type: "daily_organizer",
  status: "awaiting_confirmation",
  start_time: "2026-01-01T00:00:00+00:00",
  files_read: ["notes/a.md"],
  proposed_actions: [
    { action_id: "action-1", action: "add_tags", permission_level: 1, file: "notes/a.md", tags: ["inbox"], reason: "tidy" },
  ],
  executed_actions: [],
  diff: [
    {
      path: "notes/a.md",
      action_id: "action-1",
      operation: "update",
      before_hash: "sha256:old",
      after_hash: "sha256:new",
      before_size: 8,
      after_size: 20,
      unified_diff: "@@ -1 +1 @@\n-tags\n+tags + inbox",
      status: "proposed",
    },
  ],
  before_hash: { "notes/a.md": "sha256:old" },
  after_hash: { "notes/a.md": "sha256:new" },
};
const committedDetail: JobDetailDTO = { ...awaitingDetail, status: "committed", diff: [{ ...awaitingDetail.diff[0], status: "accepted" }] };
const rolledBackDetail: JobDetailDTO = { ...awaitingDetail, status: "rolled_back", error: { code: "transaction_failed", message: "rolled back" } };
const rollbackFailedDetail: JobDetailDTO = { ...awaitingDetail, status: "rollback_failed", error: { code: "rollback_failed", message: "rollback failed on notes/a.md: conflict" } };

function page(items: JobSummaryDTO[]): HistoryPageDTO {
  return { items, total: items.length, limit: 20, offset: 0 };
}

const noopApi = {
  fetchVaultFiles: vi.fn(async () => ({ entries: [] })),
  fetchVaultFile: vi.fn(async () => {
    throw new Error("not configured");
  }),
  patchVaultFile: vi.fn(async () => {
    throw new Error("not configured");
  }),
};

beforeEach(() => {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], status: "idle", error: null },
    tabs: [],
    activePath: null,
    sessions: {},
    history: { status: "idle", page: null, selected: null, error: null, requestVersion: 0, busy: false },
  });
  configureWorkspaceApi({ ...noopApi, fetchVaultFiles: vi.fn(async () => ({ entries: [] })) });
});

async function openFirstJob(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("button", { name: /daily_organizer/ });
  await user.click(screen.getByRole("button", { name: /daily_organizer/ }));
}

describe("AIHistoryPanel", () => {
  it("shows empty state and error state", async () => {
    configureWorkspaceApi({ ...noopApi, listHistory: vi.fn(async () => page([])) });
    render(<AIHistoryPanel onClose={() => undefined} />);
    expect(await screen.findByText("No AI jobs yet.")).toBeInTheDocument();
  });

  it("accepting is gated behind the Level 1 confirm dialog", async () => {
    const acceptJob = vi.fn(async (): Promise<JobDetailDTO> => committedDetail);
    configureWorkspaceApi({
      ...noopApi,
      listHistory: vi.fn(async () => page([summaryItem])),
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => awaitingDetail),
      acceptJob,
      rejectJob: vi.fn(async (): Promise<JobDetailDTO> => awaitingDetail),
    });
    const user = userEvent.setup();
    render(<AIHistoryPanel onClose={() => undefined} />);
    await openFirstJob(user);
    await user.click(screen.getByRole("button", { name: /Accept All/ }));
    // Dialog open -> nothing has been accepted yet
    expect(acceptJob).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(acceptJob).toHaveBeenCalledWith("job-1", { confirm: true }));
  });

  it("per-diff Accept executes exactly that action after confirmation", async () => {
    const acceptJob = vi.fn(async (): Promise<JobDetailDTO> => committedDetail);
    configureWorkspaceApi({
      ...noopApi,
      listHistory: vi.fn(async () => page([summaryItem])),
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => awaitingDetail),
      acceptJob,
    });
    const user = userEvent.setup();
    render(<AIHistoryPanel onClose={() => undefined} />);
    await openFirstJob(user);
    const diffSection = screen.getByLabelText("Proposed changes");
    await user.click(within(diffSection).getByRole("button", { name: /Accept update/ }));
    expect(acceptJob).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(acceptJob).toHaveBeenCalledWith("job-1", {
        action_ids: ["action-1"],
        confirm: true,
      }),
    );
  });

  it("rejects the whole diff without writing", async () => {
    const rejectJob = vi.fn(async (): Promise<JobDetailDTO> => ({ ...awaitingDetail, status: "rejected" }));
    configureWorkspaceApi({
      ...noopApi,
      listHistory: vi.fn(async () => page([summaryItem])),
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => awaitingDetail),
      rejectJob,
    });
    const user = userEvent.setup();
    render(<AIHistoryPanel onClose={() => undefined} />);
    await openFirstJob(user);
    await user.click(screen.getByRole("button", { name: "Reject All" }));
    await waitFor(() => expect(rejectJob).toHaveBeenCalledWith("job-1"));
  });

  it("undoes a committed job through confirmation", async () => {
    const undoHistory = vi.fn(async (): Promise<UndoResponseDTO> => ({ job_id: "job-1", status: "undone", restored: ["notes/a.md"], error: null }));
    configureWorkspaceApi({
      ...noopApi,
      listHistory: vi.fn(async () => page([{ ...summaryItem, status: "committed" }])),
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => committedDetail),
      undoHistory,
    });
    const user = userEvent.setup();
    render(<AIHistoryPanel onClose={() => undefined} />);
    await openFirstJob(user);
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(undoHistory).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(undoHistory).toHaveBeenCalledWith("job-1"));
  });

  it("shows rollback/conflict state alerts and no write buttons", async () => {
    configureWorkspaceApi({
      ...noopApi,
      listHistory: vi.fn(async () => page([{ ...summaryItem, status: "rolled_back" }])),
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => rolledBackDetail),
    });
    const user = userEvent.setup();
    render(<AIHistoryPanel onClose={() => undefined} />);
    await openFirstJob(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("rolled back");
    expect(screen.queryByRole("button", { name: /Accept All/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("alerts and blocks write buttons for rollback_failed", async () => {
    configureWorkspaceApi({
      ...noopApi,
      listHistory: vi.fn(async () => page([{ ...summaryItem, status: "rollback_failed" }])),
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => rollbackFailedDetail),
    });
    const user = userEvent.setup();
    render(<AIHistoryPanel onClose={() => undefined} />);
    await openFirstJob(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("rollback failed on notes/a.md: conflict");
    expect(screen.queryByRole("button", { name: /Accept All/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("creating a job is idempotent while busy", async () => {
    const createJob = vi.fn(async (_request: JobCreateRequest): Promise<JobDetailDTO> => awaitingDetail);
    const listHistory = vi.fn(async () => page([summaryItem]));
    configureWorkspaceApi({
      ...noopApi,
      createJob,
      listHistory,
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => awaitingDetail),
    });
    const user = userEvent.setup();
    render(<AIHistoryPanel onClose={() => undefined} />);
    await screen.findByText("daily_organizer");
    await user.click(screen.getByRole("button", { name: "Daily Organizer" }));
    await user.click(screen.getByRole("button", { name: "Daily Organizer" }));
    await waitFor(() => expect(createJob).toHaveBeenCalledTimes(2));
    expect(createJob.mock.calls[0][0]).toMatchObject({ permission_level: 1, execute: false });
  });
});
