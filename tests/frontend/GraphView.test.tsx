/**
 * M5 GraphView matrix: Sigma lifecycle via an injected renderer loader and
 * the jsdom fallback path (no WebGL context — the fallback IS the tested
 * path). No real WebGL or real sigma module is ever constructed.
 */
import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GraphResponse } from "../../packages/protocol/src";
import { SigmaGraph } from "../../packages/graph/src";

const kill = vi.fn();
const refresh = vi.fn();
let constructorOptions: Record<string, unknown> | undefined;
let constructedGraphs: unknown[] = [];
const listeners: Record<string, (payload: { node: string }) => void> = {};

class FakeSigma {
  on(event: string, listener: (payload: { node: string }) => void) {
    listeners[event] = listener;
  }
  kill = kill;
  refresh = refresh;
  constructor(_graph: unknown, _container: HTMLElement, options: Record<string, unknown>) {
    constructorOptions = options;
    constructedGraphs.push(_graph);
  }
}

const rendererLoader = async () => ({ default: FakeSigma as unknown });

function fakeWebGL() {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fake: true } as never);
}

function response(): GraphResponse {
  return {
    model: "note-tag-v1",
    scope: "global",
    root: null,
    nodes: [
      { id: "note:a", type: "note", label: "A", path: "notes/a.md", title: "A", tag: null, tag_folded: null },
      { id: "note:b", type: "note", label: "Ref B", path: "notes/Ref B.md", title: "Ref B", tag: null, tag_folded: null },
      { id: "tag:work", type: "tag", label: "工作", path: null, title: null, tag: "工作", tag_folded: "工作" },
    ],
    edges: [
      { id: "e1", source: "note:a", target: "note:b", type: "link", directed: true, raw: "[[Ref B]]", resolved_path: null, section: null, block: null, broken: false, ambiguous: false, candidates: [], context: null },
      { id: "e2", source: "note:a", target: "tag:work", type: "tag", directed: true, raw: null, resolved_path: null, section: null, block: null, broken: false, ambiguous: false, candidates: [], context: null },
      { id: "e3", source: "note:a", target: "", type: "link", directed: true, raw: "[[Cfg C]]", resolved_path: null, section: null, block: null, broken: true, ambiguous: false, candidates: [], context: null },
    ],
    page: { limit: 500, offset: 0, next_offset: null, total_nodes: 3, total_edges: 3, truncated: false },
    generated_at: "",
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  kill.mockClear();
  refresh.mockClear();
  constructorOptions = undefined;
  constructedGraphs = [];
  Object.keys(listeners).forEach((key) => delete listeners[key]);
});

describe("SigmaGraph fallback (jsdom has no WebGL)", () => {
  it("renders operational stats + lists instead of a blank canvas", async () => {
    render(<SigmaGraph response={response()} theme="light" onNodeClick={vi.fn()} />);
    await screen.findByText(/WebGL is not available/);
    expect(screen.getByRole("region", { name: /Graph fallback view/ })).toBeTruthy();
    expect(screen.getByText("Edges")).toBeTruthy();
    expect(constructedGraphs.length).toBe(0);
  });

  it("opens notes and filters tags from the fallback list", async () => {
    const onNodeClick = vi.fn();
    render(<SigmaGraph response={response()} theme="dark" onNodeClick={onNodeClick} />);
    const noteButton = await screen.findByRole("button", { name: "A" });
    await userEvent.click(noteButton);
    expect(onNodeClick).toHaveBeenCalledWith(expect.objectContaining({ type: "note", path: "notes/a.md" }));

    const tagButton = screen.getByRole("button", { name: "工作" });
    await userEvent.click(tagButton);
    expect(onNodeClick).toHaveBeenLastCalledWith(expect.objectContaining({ type: "tag", tag: "工作" }));
  });
});

describe("SigmaGraph renderer lifecycle (mocked WebGL + renderer)", () => {
  it("constructs the renderer with themed options, wires clicks and kills on unmount", async () => {
    fakeWebGL();
    const onNodeClick = vi.fn();
    const { unmount } = render(
      <SigmaGraph response={response()} theme="dark" onNodeClick={onNodeClick} loadRenderer={rendererLoader} />,
    );
    await waitFor(() => expect(constructedGraphs.length).toBe(1));
    expect(constructorOptions?.backgroundColor).toBe("#17171c");
    const graph = constructedGraphs[0] as { order: number; size: number };
    // 3 nodes added; dangling broken edge skipped -> 2 edges
    expect(graph.order).toBe(3);
    expect(graph.size).toBe(2);

    listeners["clickNode"]?.({ node: "note:b" });
    expect(onNodeClick).toHaveBeenCalledWith(expect.objectContaining({ path: "notes/Ref B.md" }));

    unmount();
    expect(kill).toHaveBeenCalledTimes(1);
  });

  it("falls back with the failure reason when the renderer constructor throws", async () => {
    fakeWebGL();
    const brokenLoader = async () => ({
      default: class BrokenSigma {
        constructor() {
          throw new Error("context creation failed");
        }
      } as unknown,
    });
    render(<SigmaGraph response={response()} theme="light" onNodeClick={vi.fn()} loadRenderer={brokenLoader} />);
    await screen.findByText(/Interactive graph could not start: context creation failed/);
    expect(screen.getByRole("region", { name: /Graph fallback view/ })).toBeTruthy();
  });
});
