/**
 * M14 RAG panel: ask the whole Vault a question, get a grounded answer plus
 * clickable sources.
 *
 * Design constraints (M14 §十一):
 * - it is *additive*: the existing AI panel (current-note actions) is untouched,
 *   and this panel is reachable from the same AI inspector tab;
 * - every source is rendered from the server response — the UI never derives a
 *   citation from model text, so a hallucinated path cannot be "clickable";
 * - clicking a source opens the note through the same ``onOpenNote`` callback
 *   the rest of the workspace uses (the editor scrolls to the heading itself
 *   when the server supports it; the path alone is always enough to open it).
 */
import { useEffect, useState } from "react";
import { Button, Icon } from "@localnote/ui";
import { useI18n } from "../i18n";
import { ApiError, fetchRagIndexStatus, ragQuery, rebuildRagIndex } from "../api/client";
import type { RagIndexStatusResponse, RagQueryResponse, RagSource } from "../api/types";

function sourceLabel(source: RagSource): string {
  const heading = source.heading_path ?? source.heading ?? "";
  return heading ? `${source.path} › ${heading}` : source.path;
}

export function RagPanel({
  onOpenNote,
}: {
  onOpenNote?: (path: string) => void;
}) {
  const { tr, errorText } = useI18n();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<RagQueryResponse | null>(null);
  const [status, setStatus] = useState<RagIndexStatusResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code?: string; message: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refreshStatus = () => {
    void fetchRagIndexStatus()
      .then(setStatus)
      .catch((cause: unknown) => {
        setStatus(null);
        setError(
          cause instanceof ApiError
            ? { code: cause.code, message: cause.message }
            : { message: tr("知识库索引状态不可用。", "The knowledge base index is unavailable.") },
        );
      });
  };

  useEffect(refreshStatus, []);

  const ask = () => {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    void ragQuery({ query: question.trim() })
      .then((response) => {
        setAnswer(response);
        if (response.degraded.includes("generation_unavailable")) {
          setNotice(
            tr(
              "生成模型未配置：以下是检索到的证据。",
              "No chat model is configured; showing the retrieved evidence instead.",
            ),
          );
        }
      })
      .catch((cause: unknown) => {
        setAnswer(null);
        setError(
          cause instanceof ApiError
            ? { code: cause.code, message: cause.message }
            : { message: errorText("unknown") },
        );
      })
      .finally(() => setBusy(false));
  };

  const rebuild = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    void rebuildRagIndex()
      .then((result) => {
        setStatus(result.status);
        setNotice(
          tr(
            `已重建索引：${result.indexed_documents} 篇笔记 / ${result.indexed_chunks} 个片段`,
            `Rebuilt: ${result.indexed_documents} note(s), ${result.indexed_chunks} chunk(s)`,
          ),
        );
      })
      .catch((cause: unknown) => {
        setError(
          cause instanceof ApiError
            ? { code: cause.code, message: cause.message }
            : { message: errorText("unknown") },
        );
      })
      .finally(() => setBusy(false));
  };

  return (
    <section className="ai-panel rag-panel" aria-label={tr("知识库检索", "Vault RAG")}>
      <p className="ai-hint">
        {tr(
          "对着整个笔记库提问：系统先检索真实片段，再据此作答并给出引用。",
          "Ask your whole Vault: LocalNote retrieves real passages first, then answers and cites them.",
        )}
      </p>
      <label className="ai-question">
        {tr("向知识库提问", "Ask the knowledge base")}
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={tr(
            "我的哪些笔记讨论过 embedding 或向量数据库？",
            "Which of my notes discuss embeddings or vector databases?",
          )}
          disabled={busy}
        />
      </label>
      <div className="ai-actions">
        <Button disabled={!question.trim() || busy} onClick={ask}>
          <Icon name="ai" size={14} />
          {tr("检索并回答", "Retrieve & answer")}
        </Button>
        <Button disabled={busy} onClick={rebuild}>
          <Icon name="refresh" size={14} />
          {tr("重建知识库索引", "Rebuild RAG index")}
        </Button>
      </div>

      {busy && (
        <p role="status" className="ai-hint">
          {tr("正在检索并生成…", "Retrieving and generating…")}
        </p>
      )}
      {notice && <p className="ai-hint">{notice}</p>}
      {error && (
        <p role="alert" className="ai-error">
          {error.code === "rag_unavailable"
            ? tr(
                "知识库索引不可用：请在设置中启用 RAG 并重建索引。",
                "RAG index unavailable: enable RAG in Settings and rebuild the index.",
              )
            : error.message}
        </p>
      )}

      {status && (
        <p className="ai-note rag-status">
          {tr("索引状态", "Index")}: <code>{status.status}</code> ·{" "}
          {tr(`${status.indexed_notes} 篇`, `${status.indexed_notes} notes`)} ·{" "}
          {tr(`${status.chunks} 个片段`, `${status.chunks} chunks`)}
          {status.embedding_model ? ` · ${status.embedding_model}` : ""}
          {status.embedding_degraded
            ? ` · ${tr("未配置 embedding 端点（仅词法检索）", "no embedding endpoint (lexical only)")}`
            : ""}
        </p>
      )}

      {answer && (
        <div className="ai-result rag-result">
          <p className="rag-answer">{answer.answer || tr("未生成总结，以下为检索证据。", "No summary was generated; showing retrieved evidence.")}</p>
          <p className="rag-meta">{tr("模型", "Model")}: <code>{answer.model || tr("不可用", "unavailable")}</code> · {tr("引用", "Citations")}: {answer.sources.length}</p>
          <dl className="rag-stats">
            <div>
              <dt>{tr("词法候选", "Keyword")}</dt>
              <dd>{answer.retrieval_stats.fts_candidates}</dd>
            </div>
            <div>
              <dt>{tr("向量候选", "Vector")}</dt>
              <dd>{answer.retrieval_stats.vector_candidates}</dd>
            </div>
            <div>
              <dt>{tr("引用片段", "Context")}</dt>
              <dd>{answer.retrieval_stats.context_chunks}</dd>
            </div>
            <div>
              <dt>{tr("检索耗时", "Retrieval")}</dt>
              <dd>{Math.round(answer.retrieval_stats.retrieval_ms)} ms</dd>
            </div>
          </dl>
          {answer.sources.length > 0 && (
            <>
              <h3>{tr("证据关系", "Evidence map")}</h3>
              <div className="rag-evidence-map" role="list" aria-label={tr("证据相关性", "Evidence relevance")}>
                {answer.sources.map((source, index) => {
                  const score = Math.max(0, Math.min(1, source.score ?? 0));
                  return <div className="rag-evidence-node" role="listitem" key={`map-${source.id}`}><span className="rag-node-index">{index + 1}</span><span className="rag-node-label">{source.heading_path ?? source.heading ?? source.path}</span><span className="rag-node-bar"><span style={{ width: `${Math.round(score * 100)}%` }} /></span><small>{source.score == null ? tr("未评分", "unscored") : score.toFixed(2)}</small></div>;
                })}
              </div>
              <h3>{tr("来源", "Sources")}</h3>
              <ul className="rag-sources">
                {answer.sources.map((source) => (
                  <li key={source.id}>
                    <button
                      type="button"
                      className="ai-related-link"
                      onClick={() => onOpenNote?.(source.path)}
                      title={tr("打开对应笔记", "Open the note")}
                    >
                      <strong>[{source.id}] {sourceLabel(source)}</strong>
                      <small>
                        {tr(
                          `${source.start_line}-${source.end_line} 行`,
                          `lines ${source.start_line}-${source.end_line}`,
                        )}
                      </small>
                    </button>
                    {source.excerpt && <p className="rag-excerpt">{source.excerpt}</p>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {answer.sources.length === 0 && <p className="ai-hint" role="status">{tr("没有找到可引用的笔记片段。", "No citable note passages were found.")}</p>}
          {answer.invalid_citations.length > 0 && (
            <p className="ai-hint">
              {tr(
                `已忽略 ${answer.invalid_citations.length} 个无效引用。`,
                `${answer.invalid_citations.length} invalid citation(s) were removed.`,
              )}
            </p>
          )}
          {answer.degraded.length > 0 && (
            <p className="ai-hint" data-testid="rag-degraded">
              {/* One string (not an interpolation of several nodes) so the line
                  is a single text node and stays testable/selectable. */}
              {`${tr("降级", "Degraded")}: ${answer.degraded.join(", ")}`}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export default RagPanel;
