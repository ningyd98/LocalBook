import { beforeEach, describe, expect, it, vi } from "vitest";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { JobDetailDTO, HistoryPageDTO, UndoResponseDTO } from "../../packages/protocol/src";

const detail: JobDetailDTO = {
  job_id: "job-1",
  task_type: "manual",
  status: "awaiting_confirmation",
  start_time: "2026-01-01T00:00:00+00:00",
  files_read: ["notes/a.md"],
  proposed_actions: [],
  executed_actions: [],
  diff: [],
  before_hash: {},
  after_hash: {},
};

function page(count: number): HistoryPageDTO {
  return {
    items: Array.from({ length: count }, (_, i) => ({
      job_id: `job-${i}`,
      task_type: "manual",
      status: "committed",
      start_time: null,
      action_count: 0,
    })),
    total: count,
    limit: 20,
    offset: 0,
  };
}

const trio = {
  fetchVaultFiles: vi.fn(async () => ({ entries: [] })),
  fetchVaultFile: vi.fn(async () => {
    throw new Error("nope");
  }),
  patchVaultFile: vi.fn(async () => {
    throw new Error("nope");
  }),
};

beforeEach(() => {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null },
    tabs: [],
    activePath: null,
    sessions: {},
    history: { status: "idle", page: null, selected: null, error: null, requestVersion: 0, busy: false },
  });
  configureWorkspaceApi({ ...trio });
});

describe("M7 isolation", () => {
  it("a job accept never touches local editor sessions or vault patches", async () => {
    const patchVaultFile = vi.fn(async () => {
      throw new Error("must not write");
    });
    const acceptJob = vi.fn(async (): Promise<JobDetailDTO> => detail);
    configureWorkspaceApi({
      ...trio,
      patchVaultFile,
      listHistory: vi.fn(async () => page(1)),
      getHistory: vi.fn(async (): Promise<JobDetailDTO> => detail),
      acceptJob,
    });
    const result = await useWorkspaceStore.getState().acceptJob("job-1", { confirm: true });
    expect(result?.job_id).toBe("job-1");
    expect(acceptJob).toHaveBeenCalledTimes(1);
    expect(patchVaultFile).not.toHaveBeenCalled();
    expect(useWorkspaceStore.getState().sessions).toEqual({});
  });

  it("stale loadHistory responses are discarded by requestVersion", async () => {
    let firstResolve!: (value: HistoryPageDTO) => void;
    configureWorkspaceApi({
      ...trio,
      listHistory: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<HistoryPageDTO>((resolve) => {
              firstResolve = resolve;
            }),
        )
        .mockImplementationOnce(async () => page(2)),
    });
    const store = useWorkspaceStore.getState();
    const first = store.loadHistory();
    await store.loadHistory();
    // late answer from the older request must be dropped
    firstResolve(page(1));
    await first;
    const state = useWorkspaceStore.getState().history;
    expect(state.status).toBe("ready");
    expect(state.page?.total).toBe(2);
  });

  it("missing history API surfaces a safe error instead of crashing", async () => {
    configureWorkspaceApi({ ...trio, listHistory: undefined });
    await useWorkspaceStore.getState().loadHistory();
    const state = useWorkspaceStore.getState().history;
    expect(state.status).toBe("error");
    expect(state.error?.message).toMatch(/not configured/i);
  });

  it("undoing through the store records no local edit", async () => {
    const undoHistory = vi.fn(async (): Promise<UndoResponseDTO> => ({ job_id: "job-1", status: "undone", restored: ["notes/a.md"], error: null }));
    configureWorkspaceApi({ ...trio, undoHistory, listHistory: vi.fn(async () => page(1)) });
    const result = await useWorkspaceStore.getState().undoHistory("job-1");
    expect(result?.status).toBe("undone");
    const sessions = useWorkspaceStore.getState().sessions;
    expect(Object.keys(sessions)).toHaveLength(0);
  });
});
