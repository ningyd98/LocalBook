import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render } from "./render";
import { Preview } from "../../packages/markdown/src/Preview";
import { renderMarkdown } from "../../packages/markdown/src/render";
import { AttachmentPreview } from "../../apps/web/src/components/AttachmentPreview";
import { EditorPane } from "../../apps/web/src/components/EditorPane";
import {
  configureWorkspaceApi,
  preferenceDefaults,
  toBase64,
  useWorkspaceStore,
} from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

type Base64Upload = NonNullable<WorkspaceApi["uploadAttachmentBase64"]>;

const audioResult = (path = "notes/recording.m4a") => ({
  path,
  sha256: "sha256:" + "a".repeat(64),
  byte_length: 4,
  content_type: "audio/mp4",
  operation: "created" as const,
  original_name: path.split("/").at(-1)!,
});

beforeEach(() => {
  useWorkspaceStore.getState().resetVault();
  useWorkspaceStore.setState({
    ...preferenceDefaults,
    tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null },
    tabs: [],
    activePath: null,
    sessions: {},
  });
});

describe("audio Markdown rendering", () => {
  it("turns an audio link into a player without allowing raw HTML", () => {
    const html = renderMarkdown("[recording.m4a](recording.m4a)", {
      resolveUrl: path => `/resource?path=${encodeURIComponent(path)}`,
      enableAudioTranscription: true,
    });
    expect(html).toMatch(/audio[^>]*controls/);
    expect(html).toContain("audio-transcribe");
    expect(html).toContain("recording.m4a");
    expect(renderMarkdown('<audio src="javascript:alert(1)"></audio>')).not.toContain("javascript:");
  });

  it("resolves the note-relative path when the transcription button is clicked", async () => {
    const onTranscribe = vi.fn();
    render(
      <Preview
        source="![recording](recording.m4a)"
        notePath="notes/today.md"
        resolveResourceUrl={path => `/resource?path=${path}`}
        onTranscribeAudio={onTranscribe}
      />,
    );
    await userClick(screen.getByRole("button", { name: /transcribe/i }));
    expect(onTranscribe).toHaveBeenCalledWith("notes/recording.m4a");
  });
});

describe("audio import and attachment viewer", () => {
  it("adds a dedicated recording picker and embeds audio uploads", async () => {
    const base64 = vi.fn<Base64Upload>(async () => audioResult());
    configureWorkspaceApi({
      fetchVaultFiles: async () => ({ entries: [] }),
      fetchVaultFile: async path => ({ path, content_base64: toBase64("# A\n"), byte_length: 4, sha256: "sha256:base", content_type: "text/markdown" }),
      patchVaultFile: async () => ({ path: "notes/a.md", sha256: "sha256:new", byte_length: 4, operation: "updated" }),
      uploadAttachmentBase64: base64,
      uploadAttachmentMultipart: async () => audioResult(),
    });
    await act(async () => { await useWorkspaceStore.getState().openFile("notes/a.md"); });
    render(<EditorPane session={useWorkspaceStore.getState().sessions["notes/a.md"]!} theme="light" onChange={vi.fn()} onSave={vi.fn()} onUploadFiles={(files, source) => void useWorkspaceStore.getState().uploadAndInsertAttachment(files[0]!, { source })}/>);
    expect(screen.getByTestId("audio-input")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("audio-input"), { target: { files: [new File([new Uint8Array([1, 2])], "recording.m4a", { type: "audio/mp4" })] } });
    await waitFor(() => expect(base64).toHaveBeenCalled());
    expect(useWorkspaceStore.getState().sessions["notes/a.md"]!.content).toContain("![recording.m4a](recording.m4a)");
  });

  it("plays audio and displays the local transcript", async () => {
    const onTranscribe = vi.fn(async () => ({ ...audioResult("recording.m4a"), text: "hello from whisper", language: "en", tool: "whisper", truncated: false }));
    render(<AttachmentPreview path="recording.m4a" resolveResourceUrl={path => `/resource?path=${path}`} onTranscribe={onTranscribe}/>);
    expect(screen.getByRole("button", { name: /transcribe/i })).toBeInTheDocument();
    await userClick(screen.getByRole("button", { name: /transcribe/i }));
    await waitFor(() => expect(screen.getByText("hello from whisper")).toBeInTheDocument());
    expect(onTranscribe).toHaveBeenCalledWith("recording.m4a");
  });

  it("keeps transcription available when browser audio decoding fails", async () => {
    const onTranscribe = vi.fn(async () => ({ ...audioResult("recording.m4a"), text: "decoded locally", language: null, tool: "whisper", truncated: false }));
    const { container } = render(<AttachmentPreview path="recording.m4a" resolveResourceUrl={path => `/resource?path=${path}`} onTranscribe={onTranscribe}/>);
    fireEvent.error(container.querySelector("audio")!);
    const button = screen.getByRole("button", { name: /transcribe/i });
    expect(button).not.toBeDisabled();
    await userClick(button);
    await waitFor(() => expect(screen.getByText("decoded locally")).toBeInTheDocument());
  });
});

async function userClick(element: HTMLElement) {
  fireEvent.click(element);
  await act(async () => undefined);
}
