import { useState } from "react";
import { Button } from "@localnote/ui";
import { useWorkspaceStore } from "@localnote/workspace";

export function AIPanel({ onClose, onOpenNote }: { onClose: () => void; onOpenNote?: (path: string) => void }) {
  const path = useWorkspaceStore((s) => s.activePath);
  const ai = useWorkspaceStore((s) => s.ai);
  const runAI = useWorkspaceStore((s) => s.runAI);
  const [question, setQuestion] = useState("");
  const act = (action: "ask" | "summarize" | "tags" | "related") => {
    if (!path || ai.status === "loading") return;
    void runAI(action, action === "ask" ? { note_path: path, question } : { note_path: path, ...(action === "related" ? { limit: 5 } : {}) });
  };
  const response = ai.response as any;
  return <aside className="ai-panel" aria-label="AI assistant">
    <header className="ai-panel-header"><h2>AI Assistant</h2><Button onClick={onClose} aria-label="Close AI panel">Close</Button></header>
    {!path ? <p className="ai-hint">Open a Markdown note to use AI.</p> : <>
      <p className="ai-note">Current note: <code>{path}</code></p>
      <label className="ai-question">Ask this note<textarea value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask a question…" /></label>
      <div className="ai-actions"><Button disabled={!question.trim() || ai.status === "loading"} onClick={() => act("ask")}>Ask</Button><Button disabled={ai.status === "loading"} onClick={() => act("summarize")}>Summarize</Button><Button disabled={ai.status === "loading"} onClick={() => act("tags")}>Generate Tags</Button><Button disabled={ai.status === "loading"} onClick={() => act("related")}>Suggest Related</Button></div>
      {ai.status === "loading" && <p role="status">Thinking…</p>}
      {ai.status === "offline" && <p role="alert" className="ai-error">AI is offline or unavailable.</p>}
      {ai.error && ai.status === "error" && <p role="alert" className="ai-error">{ai.error.message}</p>}
      {response && <div className="ai-result">{"answer" in response && <p>{response.answer}</p>}{"summary" in response && <p>{response.summary}</p>}{"key_points" in response && <ul>{response.key_points.map((v: string) => <li key={v}>{v}</li>)}</ul>}{"tags" in response && <><h3>Suggested tags</h3><ul>{response.tags.map((v: {name:string;reason:string}) => <li key={v.name}><strong>{v.name}</strong> — {v.reason}</li>)}</ul></>}{"related" in response && <><h3>Related notes</h3><ul>{response.related.map((v: {path:string;title:string;reason:string}) => <li key={v.path}><button type="button" className="ai-related-link" onClick={() => onOpenNote?.(v.path)}><strong>{v.title}</strong> <small>{v.path}</small></button><br />{v.reason}</li>)}</ul></>}{"citations" in response && response.citations.length > 0 && <small>{response.citations.length} citation(s)</small>}</div>}
    </>}
  </aside>;
}
