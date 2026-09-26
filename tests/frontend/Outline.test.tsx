/**
 * Outline (目录视图).
 *
 * `parseHeadings` is the whole document model of the feature, so it is tested
 * directly (levels, source lines, fenced code, frontmatter, setext, inline
 * syntax); the panel tests then pin the two behaviours a user notices — the
 * heading list with its active section marker, and what a click reports.
 */
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { OutlinePanel } from "../../apps/web/src/components/OutlinePanel";
import { buildOutlineTree, findOutlineNode, parseHeadings } from "../../packages/protocol/src";

describe("parseHeadings", () => {
  it("collects ATX headings with their level and 0-based line", () => {
    const headings = parseHeadings("# Title\n\nbody\n\n## Section\n### Deep\n");

    expect(headings).toEqual([
      { level: 1, text: "Title", line: 0 },
      { level: 2, text: "Section", line: 4 },
      { level: 3, text: "Deep", line: 5 },
    ]);
  });

  it("never treats text inside a fenced block as a heading", () => {
    const source = "# Real\n\n```md\n# Not a heading\n```\n\n~~~\n## Also not\n~~~\n\n## After\n";

    expect(parseHeadings(source).map(heading => heading.text)).toEqual(["Real", "After"]);
  });

  it("skips the frontmatter block so YAML comments stay out of the outline", () => {
    const source = "---\ntitle: A\n# a yaml comment\n---\n\n# Body\n";

    expect(parseHeadings(source)).toEqual([{ level: 1, text: "Body", line: 5 }]);
  });

  it("recognises setext headings and consumes the underline", () => {
    const source = "Title\n=====\n\nSub\n---\n\n# Next\n";

    expect(parseHeadings(source)).toEqual([
      { level: 1, text: "Title", line: 0 },
      { level: 2, text: "Sub", line: 3 },
      { level: 1, text: "Next", line: 6 },
    ]);
  });

  it("requires the space after #, exactly like CommonMark", () => {
    expect(parseHeadings("#NoSpace\n# yes\n")).toEqual([{ level: 1, text: "yes", line: 1 }]);
  });

  it("reduces inline syntax to the text a reader sees", () => {
    const headings = parseHeadings("## **Bold** and `code`\n## [[Target|Alias]] link\n## [Text](https://x) ##\n");

    expect(headings.map(heading => heading.text)).toEqual(["Bold and code", "Alias link", "Text"]);
  });

  it("removes color-formatting HTML from the outline label", () => {
    const headings = parseHeadings("## **<span style=\"color:#e5484d\">下一步工作要求</span>**\n");

    expect(headings[0]?.text).toBe("下一步工作要求");
  });

  it("keeps an empty heading because it still marks a position", () => {
    expect(parseHeadings("##\n")).toEqual([{ level: 2, text: "", line: 0 }]);
  });

  it("handles CRLF documents", () => {
    expect(parseHeadings("# A\r\n\r\n## B\r\n")).toEqual([
      { level: 1, text: "A", line: 0 },
      { level: 2, text: "B", line: 2 },
    ]);
  });
});

describe("OutlinePanel", () => {
  const source = "# Title\n\ntext\n\n## First\n\n## Second\n";
  /** Heading rows only — the expand/collapse toggles are buttons too. */
  const headingButtons = () => within(screen.getByRole("navigation", { name: "Note headings" })).getAllByRole("button", { name: /^H\d/ });

  it("lists every heading with its level", () => {
    render(<OutlinePanel path="Notes/A.md" source={source} onJump={() => undefined}/>);

    expect(headingButtons().map(item => item.textContent)).toEqual(["H1Title", "H2First", "H2Second"]);
  });

  it("reports the clicked heading and its index", async () => {
    const onJump = vi.fn();
    render(<OutlinePanel path="Notes/A.md" source={source} onJump={onJump}/>);

    await userEvent.click(screen.getByRole("button", { name: /Second/ }));

    expect(onJump).toHaveBeenCalledTimes(1);
    expect(onJump.mock.calls[0]![0]).toEqual({ level: 2, text: "Second", line: 6 });
    expect(onJump.mock.calls[0]![1]).toBe(2);
  });

  it("marks the section the caret sits in", () => {
    const { rerender } = render(<OutlinePanel path="Notes/A.md" source={source} cursorLine={0} onJump={() => undefined}/>);

    expect(screen.getByRole("button", { name: /Title/ })).toHaveAttribute("aria-current", "location");

    // Between "First" (line 4) and "Second" (line 6) the caret belongs to First.
    rerender(<OutlinePanel path="Notes/A.md" source={source} cursorLine={5} onJump={() => undefined}/>);

    expect(screen.getByRole("button", { name: /First/ })).toHaveAttribute("aria-current", "location");
    expect(screen.getByRole("button", { name: /Title/ })).not.toHaveAttribute("aria-current");
  });

  it("follows live edits without a reload", () => {
    // NB: multi-line sources are passed as expressions — a JSX string attribute
    // would keep `\n` literal.
    const { rerender } = render(<OutlinePanel path="Notes/A.md" source={"# One\n"} onJump={() => undefined}/>);
    expect(headingButtons()).toHaveLength(1);

    rerender(<OutlinePanel path="Notes/A.md" source={"# One\n\n## Two\n"} onJump={() => undefined}/>);

    expect(headingButtons()).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Two/ })).toBeInTheDocument();
  });

  it("explains both empty states", () => {
    const { rerender } = render(<OutlinePanel path={null} source="" onJump={() => undefined}/>);
    expect(screen.getByRole("status")).toHaveTextContent("Open a note");

    rerender(<OutlinePanel path="Notes/A.md" source={"no headings here\n"} onJump={() => undefined}/>);
    expect(screen.getByRole("status")).toHaveTextContent("no headings yet");
  });

  it("keeps a note path with no open session from crashing", () => {
    render(<OutlinePanel path="Notes/A.md" source="" onJump={() => undefined}/>);
    // No headings yet, but the panel still renders and stays interactive.
    expect(screen.getByRole("status")).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
  });
});

describe("buildOutlineTree", () => {
  const tree = () => buildOutlineTree(parseHeadings("# A\n## B\n### C\n## D\n# E\n"));

  it("nests each heading under the closest shallower one", () => {
    const [a, e] = tree();

    expect(a!.key).toBe("0");
    expect(a!.children.map(node => node.heading.text)).toEqual(["B", "D"]);
    expect(a!.children[0]!.key).toBe("0.0");
    expect(a!.children[0]!.children.map(node => node.heading.text)).toEqual(["C"]);
    expect(e!.key).toBe("1");
    expect(e!.children).toEqual([]);
  });

  it("keeps the flat heading index so a click can still address the preview", () => {
    const nodes = tree();

    expect(nodes[0]!.index).toBe(0);
    expect(nodes[0]!.children[1]!.index).toBe(3); // "D" is the 4th heading
    expect(findOutlineNode(nodes, 2)!.heading.text).toBe("C");
    expect(findOutlineNode(nodes, 99)).toBeNull();
  });

  it("nests a level jump without inventing intermediate nodes", () => {
    const [root] = buildOutlineTree(parseHeadings("# A\n### C\n"));

    expect(root!.children).toHaveLength(1);
    expect(root!.children[0]!.heading.level).toBe(3);
  });
});

describe("OutlinePanel collapse", () => {
  const source = "# A\ntext\n## B\ntext\n### C\ntext\n## D\n";
  const noop = () => undefined;
  /** Heading rows only — the expand/collapse toggles are buttons too. */
  const headingButtons = () => within(screen.getByRole("navigation", { name: "Note headings" })).getAllByRole("button", { name: /^H\d/ });
  /** Just the heading labels: a folded row also carries a "hidden" badge. */
  const headingLabels = () => headingButtons().map(button => button.querySelector(".outline-text")?.textContent);

  it("asks to collapse the section whose toggle was clicked", async () => {
    const onToggle = vi.fn();
    render(<OutlinePanel path="Notes/A.md" source={source} onToggle={onToggle} onJump={noop}/>);

    // Two toggles (A and B); the first one in tree order belongs to A.
    await userEvent.click(screen.getAllByRole("button", { name: "Collapse" })[0]!);

    expect(onToggle).toHaveBeenCalledWith("0");
  });

  it("hides every descendant of a collapsed heading", () => {
    const { rerender } = render(<OutlinePanel path="Notes/A.md" source={source} onToggle={noop} onJump={noop}/>);
    expect(headingButtons()).toHaveLength(4);

    rerender(<OutlinePanel path="Notes/A.md" source={source} collapsed={new Set(["0"])} onToggle={noop} onJump={noop}/>);

    expect(headingLabels()).toEqual(["A"]);
    // The folded row reports how many headings it is hiding (B, C and D).
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("folds one level at a time", () => {
    render(<OutlinePanel path="Notes/A.md" source={source} collapsed={new Set(["0.0"])} onToggle={noop} onJump={noop}/>);

    expect(headingLabels()).toEqual(["A", "B", "D"]);
    expect(screen.getAllByRole("button", { name: "Expand" })).toHaveLength(1);
  });

  it("offers no toggle on a leaf heading", () => {
    render(<OutlinePanel path="Notes/A.md" source={"# A\n## B\n"} onToggle={noop} onJump={noop}/>);

    const toggles = screen.getAllByRole("button", { name: /Collapse|Expand/ });
    expect(toggles).toHaveLength(1); // A only — B has no children
    expect(toggles[0]).toHaveAttribute("aria-expanded", "true");
  });

  it("never marks an unrelated row as current when the caret is inside a fold", () => {
    render(<OutlinePanel path="Notes/A.md" source={source} cursorLine={5} collapsed={new Set(["0"])} onToggle={noop} onJump={noop}/>);

    // The caret is in "C" (line 4), which is folded away: nothing claims it.
    expect(screen.queryByRole("button", { current: "location" })).toBeNull();
  });

  it("stays readable when the caller owns no collapse state", () => {
    render(<OutlinePanel path="Notes/A.md" source={source} onJump={noop}/>);

    // Without `onToggle` the tree is simply always expanded.
    expect(headingButtons()).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /Collapse|Expand/ })).toBeNull();
  });
});
