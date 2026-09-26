import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { AttachmentPreview } from "../../apps/web/src/components/AttachmentPreview";
import { AttachmentToolbar } from "../../apps/web/src/components/AttachmentToolbar";
import { Preview } from "../../packages/markdown/src/Preview";
import { renderMarkdown } from "../../packages/markdown/src/render";
import { CodeMirrorEditor } from "../../packages/editor/src/CodeMirrorEditor";

const NL = String.fromCharCode(10);


describe("document attachment rendering", () => {
  it("turns Office links into a PDF iframe with an original download link", () => {
    const html = renderMarkdown("[report.docx](report.docx)", {
      resolveUrl: path => `/raw/${path}`,
      resolveDocumentUrl: path => `/preview/${path}`,
    });

    expect(html).toContain("document-attachment");
    expect(html).toContain('src="/preview/report.docx"');
    expect(html).toContain('href="/raw/report.docx"');
    expect(html).not.toContain("javascript:");
  });

  it("rejects unsafe generated document preview URLs", () => {
    const html = renderMarkdown("[report.docx](report.docx)", {
      resolveUrl: path => `/raw/${path}`,
      resolveDocumentUrl: () => "//evil.example/preview.pdf",
    });

    expect(html).not.toContain("document-attachment");
    expect(html).toContain('href="/raw/report.docx"');
  });

  it("resolves nested document references and preserves a safe PDF page fragment", () => {
    render(
      <Preview
        source="[report.docx](report.docx#page=2)"
        notePath="notes/today.md"
        resolveResourceUrl={path => `/raw?path=${path}`}
        resolveDocumentPreviewUrl={path => `/preview?path=${path}`}
      />,
    );

    expect(screen.getByTitle("report.docx")).toHaveAttribute(
      "src",
      "/preview?path=notes/report.docx#page=2",
    );
  });

  it("shows a document iframe and keeps the original download available", () => {
    render(
      <AttachmentPreview
        path="notes/report.docx"
        resolveResourceUrl={path => `/raw?path=${path}`}
        resolveDocumentPreviewUrl={path => `/preview?path=${path}`}
      />,
    );

    expect(screen.getByTitle("report.docx")).toHaveAttribute("src", "/preview?path=notes/report.docx");
    expect(screen.getByRole("link", { name: /download original/i })).toHaveAttribute(
      "href",
      "/raw?path=notes/report.docx",
    );
  });

  it("renders a document widget in live preview", async () => {
    const { container } = render(
      <CodeMirrorEditor
        value={["[report.docx](report.docx)", "# End"].join(NL) + NL}
        theme="light"
        onChange={vi.fn()}
        livePreview={{
          notePath: "notes/today.md",
          resolveResourceUrl: path => `/raw/${path}`,
          resolveDocumentUrl: path => `/preview/${path}`,
        }}
      />,
    );
    const content = container.querySelector(".cm-content") as unknown as {
      cmView: { view: { state: { doc: { length: number } }; dispatch: (spec: unknown) => void } };
    };
    content.cmView.view.dispatch({ selection: { anchor: content.cmView.view.state.doc.length } });
    await waitFor(() => expect(container.querySelector(".cm-lp-document iframe")).toBeTruthy());
    expect(container.querySelector(".cm-lp-document iframe")).toHaveAttribute("src", "/preview/report.docx");
  });

  it("adds a dedicated document picker without removing the generic picker", () => {
    const onFiles = vi.fn();
    render(<AttachmentToolbar onFiles={onFiles} />);

    expect(screen.getByTestId("attachment-input")).toBeInTheDocument();
    expect(screen.getByTestId("document-input")).toHaveAttribute("accept", expect.stringContaining(".docx"));
    fireEvent.click(screen.getByRole("button", { name: /insert document/i }));
  });
});
