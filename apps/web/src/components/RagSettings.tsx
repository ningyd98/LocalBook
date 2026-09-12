import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Icon } from "@localnote/ui";
import { useI18n } from "../i18n";
import { ragPatchPayload, testReranker, updateRagSettings } from "../api/settings";
import type { RagConfiguration, RerankerProbeResult, ServiceSettings } from "../api/settings";
import { fetchRagIndexStatus, rebuildRagIndex } from "../api/client";
import type { RagIndexStatusResponse } from "../api/types";

/**
 * The RAG defaults this section starts from live in ``../api/ragDefaults``.
 *
 * They mirror ``server.config.RagSettings``, they are a contract with the
 * server, and they deliberately do not live here: a parent importing them from
 * this component created a parent↔child value cycle that survives today but
 * breaks under Fast Refresh and whenever the defaults move.
 */

/**
 * M14 RAG settings section.
 *
 * Shows the derived-index status (notes/chunks/embedding model/pending/failed),
 * lets the user pick an embedding endpoint that is independent from the chat
 * model, and offers the explicit "rebuild" action. Saving changes only rebuilds
 * the derived RAG layer — the Vault, its watcher and every note are untouched.
 */
export function RagSettingsSection({
  rag,
  current,
  busy,
  onSaved,
  run,
  say,
}: {
  rag: RagConfiguration;
  current: ServiceSettings | null;
  busy: boolean;
  onSaved: (settings: ServiceSettings) => void;
  run: (operation: () => Promise<void>) => Promise<void>;
  say: (message: string | null) => void;
}) {
  const { tr, errorText } = useI18n();
  const [draft, setDraft] = useState<RagConfiguration>(rag);
  const [apiKey, setApiKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [status, setStatus] = useState<RagIndexStatusResponse | null>(null);
  const [rerankerProbe, setRerankerProbe] = useState<RerankerProbeResult | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [localBusy, setLocalBusy] = useState(false);

  // Re-sync the draft when the STORED configuration changes — never on object
  // identity. ``GET /settings`` hands back a fresh ``rag`` object per response
  // and this section's parent re-renders for unrelated reasons (notices, errors,
  // busy flags, section switches); an identity-keyed effect re-fires on all of
  // them and silently discards whatever the user has typed but not yet saved.
  // The key below changes only when the stored revision or stored content does.
  const storedKey = useMemo(
    () => `${current?.revision ?? "none"}:${JSON.stringify(rag)}`,
    [current?.revision, rag],
  );
  const latestRag = useRef(rag);
  latestRag.current = rag;
  useEffect(() => {
    setDraft(latestRag.current);
  }, [storedKey]);

  const refreshStatus = () =>
    void fetchRagIndexStatus()
      .then(response => { setStatus(response); setStatusError(null); })
      .catch((cause: unknown) =>
        setStatusError(cause instanceof Error ? cause.message : errorText("unknown")),
      );

  useEffect(() => {
    refreshStatus();
    // Status is server-derived; refresh when the section opens and after saves.
  }, []);

  const set = <K extends keyof RagConfiguration>(key: K, value: RagConfiguration[K]) =>
    setDraft(current => ({ ...current, [key]: value }));

  const save = () =>
    void run(async () => {
      setLocalBusy(true);
      try {
        // The body carries the server-declared key set only: the snapshot draft
        // is wider than the PATCH model, and the server's extra="forbid" rejects
        // undeclared keys (a raw spread used to 422 every save). Status-only keys
        // such as ``link_retrieval`` can never reach the wire through this helper.
        const payload = ragPatchPayload(
          draft,
          keyTouched ? { embedding_api_key: apiKey } : {},
        );
        const result = await updateRagSettings(payload, current?.revision ?? 0);
        if (result.rag) setDraft(result.rag);
        setKeyTouched(false);
        setApiKey("");
        onSaved(result);
        refreshStatus();
        say(
          tr(
            "已保存 RAG 配置并重建派生索引（未改动任何笔记）。",
            "RAG settings saved; the derived index was rebuilt (no note was modified).",
          ),
        );
      } finally {
        setLocalBusy(false);
      }
    });

  const rebuild = () =>
    void run(async () => {
      setLocalBusy(true);
      try {
        const result = await rebuildRagIndex();
        setStatus(result.status);
        say(
          tr(
            `已重建 RAG 索引：${result.indexed_documents} 篇笔记 / ${result.indexed_chunks} 个片段`,
            `RAG index rebuilt: ${result.indexed_documents} note(s), ${result.indexed_chunks} chunk(s)`,
          ),
        );
      } finally {
        setLocalBusy(false);
      }
    });

  const disabled = busy || localBusy;

  return (
    <div className="rag-settings" aria-label={tr("RAG 设置", "RAG settings")}>
      <div className="setting-row">
        <div>
          <label htmlFor="rag-enabled">{tr("启用 RAG", "Enable RAG")}</label>
          <small>
            {tr(
              "关闭后不建立索引也不检索，其余功能不受影响。",
              "When off, nothing is indexed or retrieved; everything else keeps working.",
            )}
          </small>
        </div>
        <input
          className="switch"
          id="rag-enabled"
          type="checkbox"
          role="switch"
          checked={draft.enabled}
          disabled={disabled}
          onChange={event => set("enabled", event.target.checked)}
        />
      </div>

      <h4>{tr("Embedding 服务（与 Chat 模型独立）", "Embedding endpoint (independent of chat)")}</h4>
      <label className="field">
        <span>{tr("Embedding Provider", "Embedding provider")}</span>
        <select
          value={draft.embedding_provider}
          disabled={disabled}
          onChange={event =>
            set("embedding_provider", event.target.value as RagConfiguration["embedding_provider"])
          }
        >
          <option value="hash">{tr("本地回退（离线，质量有限）", "Local fallback (offline, limited)")}</option>
          <option value="openai_compatible">OpenAI-compatible</option>
          <option value="none">{tr("不使用 embedding（仅词法检索）", "No embeddings (lexical only)")}</option>
        </select>
      </label>
      <label className="field">
        <span>{tr("Embedding Base URL", "Embedding base URL")}</span>
        <input
          value={draft.embedding_base_url}
          disabled={disabled || draft.embedding_provider === "hash"}
          placeholder="http://127.0.0.1:1234/v1"
          onChange={event => set("embedding_base_url", event.target.value)}
        />
        <small>
          {tr(
            "通常是 OpenAI 兼容的 /v1 地址，可与 Chat 端点不同。",
            "Usually an OpenAI-compatible /v1 URL; it may differ from the chat endpoint.",
          )}
        </small>
      </label>
      <label className="field">
        <span>{tr("Embedding Model", "Embedding model")}</span>
        <input
          value={draft.embedding_model}
          disabled={disabled}
          placeholder="bge-m3"
          onChange={event => set("embedding_model", event.target.value)}
        />
        <small>
          {tr(
            "与 Chat 模型解耦：不要填聊天模型。换模型或维度后需要重建索引。",
            "Decoupled from the chat model — do not put a chat model here. Changing model or dimension requires a rebuild.",
          )}
        </small>
      </label>
      <label className="field">
        <span>{tr("Embedding API Key（可选）", "Embedding API key (optional)")}</span>
        <input
          type="password"
          autoComplete="off"
          value={apiKey}
          disabled={disabled}
          placeholder={
            current?.rag?.embedding_api_key_set
              ? tr("已保存，留空则保持不变", "Saved — leave blank to keep it")
              : "sk-…"
          }
          onChange={event => { setApiKey(event.target.value); setKeyTouched(true); }}
        />
      </label>

      <h4>{tr("检索与分块", "Retrieval & chunking")}</h4>
      <div className="rag-grid">
        {([
          ["chunk_target_tokens", tr("分块目标 tokens", "Chunk target tokens")],
          ["chunk_max_tokens", tr("分块上限 tokens", "Chunk max tokens")],
          ["chunk_overlap_tokens", tr("分块重叠 tokens", "Chunk overlap tokens")],
          ["fts_top_k", tr("FTS Top K", "FTS top K")],
          ["vector_top_k", tr("向量 Top K", "Vector top K")],
          ["context_top_k", tr("上下文片段数", "Context chunks")],
        ] as Array<[keyof RagConfiguration, string]>).map(([key, label]) => (
          <label className="field" key={String(key)}>
            <span>{label}</span>
            <input
              type="number"
              min={0}
              value={Number(draft[key] ?? 0)}
              disabled={disabled}
              onChange={event => set(key, Number(event.target.value) as never)}
            />
          </label>
        ))}
      </div>
      <div className="setting-row">
        <div>
          <label htmlFor="rag-link">{tr("启用链接/图谱加权检索", "Enable link/graph retrieval")}</label>
          <small>
            {tr(
              "默认关闭。开启后 wikilink 出边、backlink 入边、同标签与图谱邻居会成为额外候选参与 RRF 融合；只读派生数据，不改动任何笔记。",
              "Off by default. When on, wikilinks, backlinks, same-tag notes and graph neighbours join the RRF fusion as extra candidates; derived data only, no note is modified.",
            )}
          </small>
        </div>
        <input
          className="switch"
          id="rag-link"
          type="checkbox"
          role="switch"
          checked={draft.link_retrieval_enabled}
          disabled={disabled}
          onChange={event => set("link_retrieval_enabled", event.target.checked)}
        />
      </div>
      {draft.link_retrieval_enabled && (
        <>
          <div className="rag-grid">
            {([
              ["link_top_k", tr("链接候选数 Top K", "Link top K")],
              ["wikilink_weight", tr("Wikilink 权重", "Wikilink weight")],
              ["backlink_weight", tr("Backlink 权重", "Backlink weight")],
              ["tag_weight", tr("同标签权重", "Same-tag weight")],
              ["graph_weight", tr("图谱邻居权重", "Graph-neighbour weight")],
            ] as Array<[keyof RagConfiguration, string]>).map(([key, label]) => (
              <label className="field" key={String(key)}>
                <span>{label}</span>
                <input
                  type="number"
                  min={0}
                  max={key === "link_top_k" ? 200 : 5}
                  step={key === "link_top_k" ? 1 : 0.1}
                  value={Number(draft[key] ?? 0)}
                  disabled={disabled}
                  onChange={event => set(key, Number(event.target.value) as never)}
                />
              </label>
            ))}
          </div>
          <p className="settings-tip">
            {tr(
              "权重范围 0–5（0 表示忽略该信号），只在这条路内部排序；该路的融合权重固定为 0.5，因此链接候选只新增、不会越过直接命中。",
              "Weights range 0–5 (0 ignores a signal) and only order within this path; its fusion weight is fixed at 0.5, so link candidates are only added — they never outrank a direct hit.",
            )}
          </p>
        </>
      )}
      <div className="setting-row">
        <div>
          <label htmlFor="rag-rerank">{tr("启用重排（可选）", "Enable reranker (optional)")}</label>
          <small>
            {tr(
              "默认关闭；开启后对融合结果做一次本地重排，失败不会影响检索。",
              "Off by default. When on, the fused list is reordered locally; a failure never breaks retrieval.",
            )}
          </small>
        </div>
        <input
          className="switch"
          id="rag-rerank"
          type="checkbox"
          role="switch"
          checked={draft.reranker_enabled}
          disabled={disabled}
          onChange={event => set("reranker_enabled", event.target.checked)}
        />
      </div>
      {draft.reranker_enabled && (
        <>
          <label className="field">
            <span>{tr("重排方式", "Rerank provider")}</span>
            <select
              value={draft.reranker_provider}
              disabled={disabled}
              onChange={event => set("reranker_provider", event.target.value)}
            >
              <option value="lexical">{tr("本地启发式（无外部依赖）", "Local heuristic (no dependency)")}</option>
              <option value="openai_compatible">OpenAI-compatible /rerank</option>
            </select>
          </label>
          {["openai_compatible", "openai"].includes(draft.reranker_provider) && (
            <>
              <label className="field">
                <span>{tr("重排 Base URL", "Reranker base URL")}</span>
                <input
                  value={draft.reranker_base_url}
                  disabled={disabled}
                  placeholder="http://127.0.0.1:9997/v1"
                  onChange={event => set("reranker_base_url", event.target.value)}
                />
                <small>
                  {tr(
                    "请求发送到 {base}/rerank（Jina/Cohere/vLLM 风格）。",
                    "Posts to {base}/rerank (Jina/Cohere/vLLM style).",
                  ).replace("{base}", draft.reranker_base_url || "{base}")}
                </small>
              </label>
              <label className="field">
                <span>{tr("重排模型", "Reranker model")}</span>
                <input
                  value={draft.reranker_model}
                  disabled={disabled}
                  placeholder="bge-reranker-v2-m3"
                  onChange={event => set("reranker_model", event.target.value)}
                />
              </label>
              <div className="setting-row">
                <div>
                  <label>{tr("测试重排端点", "Test rerank endpoint")}</label>
                  <small>
                    {rerankerProbe
                      ? rerankerProbe.status === "connected"
                        ? tr(`连接成功：${rerankerProbe.message}`, `Connected: ${rerankerProbe.message}`)
                        : tr(`不可用：${rerankerProbe.message}（检索仍可用）`, `Unavailable: ${rerankerProbe.message} (retrieval still works)`)
                      : tr("重排是可选项，失败只会降级到融合排序。", "Reranking is optional; a failure only falls back to the fused order.")}
                  </small>
                </div>
                <Button
                  disabled={disabled || !draft.reranker_base_url || !draft.reranker_model}
                  onClick={() =>
                    void run(async () => {
                      setRerankerProbe(
                        await testReranker({
                          base_url: draft.reranker_base_url,
                          model: draft.reranker_model,
                        }),
                      );
                    })
                  }
                >
                  <Icon name="refresh" size={15} />
                  {tr("测试重排", "Test rerank")}
                </Button>
              </div>
            </>
          )}
        </>
      )}

      <div className="rag-status-card">
        <h4>{tr("索引状态", "Index status")}</h4>
        {statusError && <p className="settings-tip" role="alert">{statusError}</p>}
        {status && (
          <>
            <ul className="rag-status-list">
              <li>{tr("已索引笔记", "Indexed notes")}: <strong>{status.indexed_notes}</strong></li>
              <li>{tr("片段数", "Chunks")}: <strong>{status.chunks}</strong></li>
              <li>{tr("已嵌入片段", "Embedded chunks")}: <strong>{status.embedded_chunks}</strong></li>
              <li>{tr("状态", "Status")}: <strong>{status.status}</strong></li>
              <li>{tr("向量内核", "Vector kernel")}: <strong>{status.vector_kernel || "—"}</strong></li>
              {/* Only rendered when the retriever actually reports a link path:
                  "" means "no link path wired", so there is nothing to claim. */}
              {status.link_retrieval && (
                <li>{tr("链接检索", "Link retrieval")}: <strong>{status.link_retrieval}</strong></li>
              )}
              <li>{tr("Embedding 模型", "Embedding model")}: <strong>{status.embedding_model || "—"}</strong></li>
              <li>{tr("上次索引", "Last indexed")}: <strong>{status.last_indexed ? new Date(status.last_indexed).toLocaleString() : "—"}</strong></li>
              <li>{tr("待处理", "Pending")}: <strong>{status.pending}</strong></li>
              <li>{tr("失败", "Failed")}: <strong>{status.failed}</strong></li>
            </ul>
            {status.embedding_degraded && (
              <p className="settings-tip">
                {tr(
                  "当前使用本地回退 embedder：语义检索质量有限，建议配置独立 embedding 端点。",
                  "The local fallback embedder is in use: semantic quality is limited. Configure a real embedding endpoint.",
                )}
              </p>
            )}
            {status.link_retrieval && status.link_retrieval !== "enabled" && (
              <p className="settings-tip">
                {tr(
                  `链接检索当前未生效：${status.link_retrieval}。`,
                  `Link retrieval is not active: ${status.link_retrieval}.`,
                )}
              </p>
            )}
            {status.message && <p className="settings-tip">{status.message}</p>}
          </>
        )}
        <div className="settings-actions">
          <Button disabled={disabled} onClick={rebuild}>
            <Icon name="refresh" size={15} />
            {tr("重建 RAG 索引", "Rebuild RAG index")}
          </Button>
          <Button className="primary" disabled={!current || disabled} onClick={save}>
            {localBusy ? tr("处理中…", "Working…") : tr("保存 RAG 配置", "Save RAG settings")}
          </Button>
        </div>
      </div>
    </div>
  );
}
