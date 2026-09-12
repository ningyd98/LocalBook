import { useEffect, useMemo, useState } from "react";
import { Button, Icon } from "@localnote/ui";
import { useI18n } from "../i18n";
import {
  activateAIProfile, deleteAIProfile, fetchAIProfiles, saveAIProfile, testAIProfile,
} from "../api/settings";
import type {
  AIConnectionTest, AIConfiguration, AIProviderPayload, AIProviderProfile, ServiceSettings,
} from "../api/settings";
import { ConfirmDialog } from "./ConfirmDialog";

/** Display presets (PLAN-PROVIDERS D8): the backend only knows the closed kind set. */
export interface ProviderPreset {
  key: string; label: string; kind: AIProviderProfile["kind"];
  base_url: string; chat_model: string; hint: string;
}
export function providerPresets(tr: (zh: string, en: string) => string): ProviderPreset[] {
  return [
    {key: "omlx", label: tr("本地 oMLX / LM Studio", "Local oMLX / LM Studio"), kind: "omlx",
     base_url: "http://127.0.0.1:8000/v1", chat_model: "auto",
     hint: tr("本机 OpenAI 兼容服务，无需 API Key。", "A local OpenAI-compatible server; no API key needed.")},
    {key: "openai", label: "OpenAI", kind: "openai",
     base_url: "https://api.openai.com/v1", chat_model: "gpt-4o-mini",
     hint: tr("官方 OpenAI API，需要 API Key。", "The official OpenAI API; an API key is required.")},
    {key: "deepseek", label: "DeepSeek", kind: "openai",
     base_url: "https://api.deepseek.com/v1", chat_model: "deepseek-chat",
     hint: tr("DeepSeek 开放平台，OpenAI 兼容。", "DeepSeek's OpenAI-compatible open platform.")},
    {key: "moonshot", label: "Moonshot / Kimi", kind: "openai",
     base_url: "https://api.moonshot.cn/v1", chat_model: "moonshot-v1-8k",
     hint: tr("月之暗面（Kimi），OpenAI 兼容。", "Moonshot (Kimi), OpenAI-compatible.")},
    {key: "custom", label: tr("自定义 OpenAI 兼容端点", "Custom OpenAI-compatible endpoint"), kind: "openai-compatible",
     base_url: "", chat_model: "auto",
     hint: tr("任何 OpenAI 兼容网关（vLLM、Ollama、自建代理等）。", "Any OpenAI-compatible gateway (vLLM, Ollama, a self-hosted proxy…).")},
  ];
}

/** A slug id derived from a display name, matching the server's id contract. */
export function profileIdFrom(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[^a-z0-9]+/, "").slice(0, 64);
  return slug || "provider";
}

const KIND_LABELS: Array<[AIProviderProfile["kind"], string]> = [
  ["omlx", "oMLX"], ["openai", "OpenAI"], ["openai-compatible", "OpenAI 兼容"], ["custom", "自定义"],
];

interface Draft {
  mode: "new" | "edit";
  id: string; name: string; kind: AIProviderProfile["kind"];
  base_url: string; chat_model: string; api_key: string;
  temperature: string; max_output_tokens: string; request_timeout_seconds: string;
  /** true once the user typed a key: the stored one is otherwise kept. */
  keyTouched: boolean;
}
const draftFrom = (profile: AIProviderProfile): Draft => ({
  mode: "edit", id: profile.id, name: profile.name, kind: profile.kind,
  base_url: profile.base_url ?? "", chat_model: profile.chat_model, api_key: "",
  temperature: String(profile.temperature), max_output_tokens: String(profile.max_output_tokens),
  request_timeout_seconds: String(profile.request_timeout_seconds), keyTouched: false,
});
const newDraft = (preset: ProviderPreset): Draft => ({
  mode: "new", id: "", name: preset.label, kind: preset.kind, base_url: preset.base_url,
  chat_model: preset.chat_model, api_key: "", temperature: "0.1", max_output_tokens: "1200",
  request_timeout_seconds: "60", keyTouched: false,
});

/**
 * Numeric fields the server validates (source of truth: ``ProviderPayload``).
 * A blank or out-of-range box must **not** be sent as-is: `Number("")` is `0`,
 * which passes `Number.isFinite` and then fails server-side validation
 * (`0 < 0.1` for temperature, `< 64` for tokens, `<= 0` for the timeout), and the
 * whole save came back as a 422 that the UI could only describe vaguely.
 * An omitted optional field keeps the stored value on the server, which is the
 * behaviour "I cleared this box" should have anyway.
 */
const numericFields = {
  temperature: { min: 0.1, max: 1 },
  max_output_tokens: { min: 64, max: 8192 },
  request_timeout_seconds: { min: 1, max: 120 },
} as const;
type NumericField = keyof typeof numericFields;

/** The usable number in `value`, or null when it must be left out of the payload. */
function numericOrNull(value: string, field: NumericField): number | null {
  const typed = value.trim();
  if (!typed) return null;
  const parsed = Number(typed);
  const { min, max } = numericFields[field];
  return Number.isFinite(parsed) && parsed >= min && parsed <= max ? parsed : null;
}

const NUMBER_LABELS: Record<NumericField, string> = { temperature: "温度", max_output_tokens: "最大输出 tokens", request_timeout_seconds: "请求超时（秒）" };
const NUMBER_LABELS_EN: Record<NumericField, string> = { temperature: "temperature", max_output_tokens: "max output tokens", request_timeout_seconds: "request timeout (s)" };

/** Numeric boxes whose content would be rejected, for the inline hint. */
function invalidNumericFields(draft: Draft): NumericField[] {
  return (Object.keys(numericFields) as NumericField[]).filter(field => draft[field].trim() !== "" && numericOrNull(draft[field], field) === null);
}

function payloadOf(draft: Draft, fallbackId: string): AIProviderPayload {
  const id = profileIdFrom(draft.id || draft.name || fallbackId);
  const temperature = numericOrNull(draft.temperature, "temperature");
  const maxOutputTokens = numericOrNull(draft.max_output_tokens, "max_output_tokens");
  const requestTimeout = numericOrNull(draft.request_timeout_seconds, "request_timeout_seconds");
  return {
    id,
    name: draft.name.trim() || id,
    kind: draft.kind,
    base_url: draft.base_url.trim() || null,
    chat_model: draft.chat_model.trim() || "auto",
    ...(draft.keyTouched ? {api_key: draft.api_key.trim()} : {}),
    ...(temperature !== null ? { temperature } : {}),
    ...(maxOutputTokens !== null ? { max_output_tokens: maxOutputTokens } : {}),
    ...(requestTimeout !== null ? { request_timeout_seconds: requestTimeout } : {}),
  };
}

/**
 * Provider library editor (PLAN-PROVIDERS §5.2).
 *
 * The section owns the whole multi-provider surface: switch the applied route,
 * add a route from a preset, edit one, test one, or delete an idle one. Every
 * mutation returns the full settings snapshot, which is handed back to the shell
 * so the status bar and the inspector stay in sync without a reload.
 */
export function ProviderSettingsSection(
  {ai, current, busy, onSaved, run, say}: {
    ai: AIConfiguration; current: ServiceSettings | null; busy: boolean;
    onSaved: (settings: ServiceSettings) => void; run: (operation: () => Promise<void>) => Promise<void>;
    /** Success and probe messages share the settings panel's own notice slot. */
    say: (message: string | null) => void;
  },
) {
  const {tr, errorText} = useI18n();
  const [profiles, setProfiles] = useState<AIProviderProfile[] | null>(null);
  const [activeId, setActiveId] = useState<string>(ai.active_profile_id ?? "default");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [probe, setProbe] = useState<AIConnectionTest | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const presets = useMemo(() => providerPresets(tr), [tr]);
  // Numeric boxes the server would reject: block the save and say which one.
  const invalidNumbers = draft ? invalidNumericFields(draft) : [];

  const accepted = (settings: ServiceSettings) => {
    onSaved(settings);
    if (settings.ai.profiles) setProfiles(settings.ai.profiles);
    if (settings.ai.active_profile_id) setActiveId(settings.ai.active_profile_id);
  };
  useEffect(() => {
    let alive = true;
    void fetchAIProfiles()
      .then(list => {if(alive) {setProfiles(list.profiles); setActiveId(list.active_profile_id); setListError(null);}},
            e => {if(alive) setListError(errorText(e));});
    return () => {alive = false;};
    // Re-reading on the settings revision keeps two open pages convergent.
  }, [current?.revision]);

  const revision = () => current?.revision ?? 0;
  const select = (id: string) => {say(null); setProbe(null); setDraft(null);
    void run(async()=>{accepted(await activateAIProfile(id, revision())); say(tr("已切换到该供应商。", "Switched to this provider."));});};
  const submit = (activate: boolean) => {if(!draft) return;
    void run(async()=>{
      const result = await saveAIProfile(payloadOf(draft, activeId), revision(), activate);
      accepted(result); setDraft(null); setProbe(null);
      say(activate ? tr("已保存并切换。", "Saved and switched.") : tr("已保存，稍后可切换。", "Saved; switch to it later."));
    });};
  const remove = (id: string) => {void run(async()=>{
    accepted(await deleteAIProfile(id, revision())); setDraft(null);
    say(tr("已删除该供应商档案。", "Provider profile deleted."));
  });};
  const test = () => {if(!draft) return;
    void run(async()=>{
      const result = await testAIProfile(payloadOf(draft, activeId));
      setProbe(result);
      say(result.status === "connected"
        ? tr(`连接成功 · ${result.selected_model}`, `Connected · ${result.selected_model}`)
        : tr("连接未通过，配置仍可保存。", "The probe failed; the profile can still be saved."));
    });};

  const active = profiles?.find(p => p.id === activeId) ?? null;
  return <>
    <p className="settings-tip">{tr("可保存多个 AI 供应商（端点、密钥、模型各自独立），随时一键切换；写入笔记的任务会使用当前生效的供应商。",
      "Save several AI providers (each with its own endpoint, key and model) and switch between them instantly. Note-writing tasks use the applied provider.")}</p>
    {listError && <div className="feedback error" role="alert">{listError}</div>}
    <div className="provider-layout">
      <div className="provider-list" role="list" aria-label={tr("供应商档案", "Provider profiles")}>
        {(profiles ?? []).map(profile => <div key={profile.id} role="listitem" className={`provider-row${profile.id === activeId ? " is-active" : ""}`}>
          <button type="button" className="provider-pick" disabled={busy} aria-pressed={profile.id === activeId}
                  onClick={()=>select(profile.id)}>
            <strong>{profile.name}</strong>
            <small>{KIND_LABELS.find(([kind])=>kind===profile.kind)?.[1] ?? profile.kind} · {profile.base_url ?? tr("未填写端点", "No endpoint")}</small>
            <small>{profile.chat_model} · {profile.api_key_set ? tr("已保存密钥", "Key saved") : tr("无密钥", "No key")}</small>
          </button>
          <div className="provider-row-actions">
            {profile.id === activeId
              ? <span className="provider-badge">{tr("使用中", "In use")}</span>
              : <button type="button" disabled={busy} onClick={()=>select(profile.id)}>{tr("切换", "Switch")}</button>}
            <button type="button" disabled={busy} onClick={()=>{say(null);setProbe(null);setDraft(draftFrom(profile));}}>{tr("编辑", "Edit")}</button>
            <button type="button" disabled={busy || profile.id === activeId}
                    onClick={()=>setConfirmDelete(profile.id)}>{tr("删除", "Delete")}</button>
          </div>
        </div>)}
        {profiles === null && <p className="settings-tip">{tr("正在读取供应商档案…", "Loading provider profiles…")}</p>}
      </div>
      <div className="provider-presets">
        <span>{tr("新增供应商", "Add provider")}</span>
        <div className="provider-preset-buttons">
          {presets.map(preset => <button type="button" key={preset.key} disabled={busy} title={preset.hint}
            onClick={()=>{say(null);setProbe(null);setDraft(newDraft(preset));}}>{preset.label}</button>)}
        </div>
      </div>
    </div>
    {active && !draft && <p className="settings-tip">{tr("当前生效", "Currently applied")}：<code>{active.name}</code>
      {" · "}<code>{active.base_url ?? "—"}</code>{" · "}<code>{active.chat_model}</code>
      {active.from_env && <>{" · "}{tr("端点由环境变量提供", "endpoint from environment")}</>}</p>}
    {draft && <form className="provider-form" onSubmit={e=>{e.preventDefault(); submit(true);}}>
      <h4>{draft.mode === "new" ? tr("新增供应商档案", "New provider profile") : tr(`编辑：${draft.name}`, `Edit: ${draft.name}`)}</h4>
      <label className="field"><span>{tr("档案 ID", "Profile id")}</span>
        <input value={draft.id} disabled={busy || draft.mode === "edit"} placeholder={profileIdFrom(draft.name)}
               onChange={e=>setDraft({...draft, id: e.target.value})}/>
        <small>{tr("小写字母、数字与 . _ -，用于配置文件与接口；留空则按名称生成。",
          "Lowercase letters, digits and . _ -; used in the settings file and API. Leave blank to derive it from the name.")}</small>
      </label>
      <label className="field"><span>{tr("显示名称", "Display name")}</span>
        <input value={draft.name} disabled={busy} onChange={e=>setDraft({...draft, name: e.target.value})}/>
      </label>
      <label className="field"><span>{tr("供应商类型", "Provider kind")}</span>
        <select value={draft.kind} disabled={busy} onChange={e=>setDraft({...draft, kind: e.target.value as Draft["kind"]})}>
          {KIND_LABELS.map(([kind, label])=><option key={kind} value={kind}>{label}</option>)}
        </select>
      </label>
      <label className="field"><span>{tr("服务端点", "Endpoint")}</span>
        <input value={draft.base_url} disabled={busy} placeholder="https://api.example.com/v1"
               onChange={e=>{setDraft({...draft, base_url: e.target.value}); setProbe(null);}}/>
        <small>{tr("OpenAI 兼容 API 的基础地址，通常以 /v1 结尾。", "An OpenAI-compatible base URL, usually ending in /v1.")}</small>
      </label>
      <label className="field"><span>{tr("API Key（可选）", "API key (optional)")}</span>
        <input type="password" autoComplete="off" value={draft.api_key} disabled={busy}
               placeholder={draft.mode === "edit"
                 ? (profiles?.find(p=>p.id===draft.id)?.api_key_set ? tr("已保存，留空则保持不变", "Saved — leave blank to keep it") : "sk-…")
                 : "sk-…"}
               onChange={e=>{setDraft({...draft, api_key: e.target.value, keyTouched: true}); setProbe(null);}}/>
        <small>{tr("以 Bearer 方式发送，仅保存在本机配置文件，接口不会回显。", "Sent as a Bearer token, stored only in the local config file and never returned by the API.")}</small>
      </label>
      <label className="field"><span>{tr("模型", "Model")}</span>
        <input value={draft.chat_model} disabled={busy} onChange={e=>{setDraft({...draft, chat_model: e.target.value}); setProbe(null);}}/>
        <small>{tr("填写 auto 自动选择，或输入准确的模型 ID。", "Use auto for automatic selection, or enter an exact model ID.")}</small>
      </label>
      <div className="provider-form-grid">
        <label className="field"><span>{tr("温度", "Temperature")}</span>
          <input type="number" min={0} max={1} step={0.05} value={draft.temperature} disabled={busy}
                 onChange={e=>setDraft({...draft, temperature: e.target.value})}/></label>
        <label className="field"><span>{tr("最大输出", "Max output tokens")}</span>
          <input type="number" min={64} max={8192} step={64} value={draft.max_output_tokens} disabled={busy}
                 onChange={e=>setDraft({...draft, max_output_tokens: e.target.value})}/></label>
        <label className="field"><span>{tr("生成超时（秒）", "Generation timeout (s)")}</span>
          <input type="number" min={1} max={120} step={1} value={draft.request_timeout_seconds} disabled={busy}
                 onChange={e=>setDraft({...draft, request_timeout_seconds: e.target.value})}/></label>
      </div>
      {invalidNumbers.length > 0 && <p className="feedback error" role="alert" data-testid="provider-number-error">
        {tr(
          `以下数值超出允许范围，请修正后再保存：${invalidNumbers.map(field => `${NUMBER_LABELS[field]}（${numericFields[field].min}–${numericFields[field].max}）`).join("、")}`,
          `These values are out of range; fix them before saving: ${invalidNumbers.map(field => `${NUMBER_LABELS_EN[field]} (${numericFields[field].min}–${numericFields[field].max})`).join(", ")}`,
        )}</p>}
      {probe && <p className="settings-tip" role="status">{probe.status === "connected"
        ? tr(`连接成功，发现 ${probe.models.length} 个模型。`, `Connected; ${probe.models.length} model(s) discovered.`)
        : probe.error_code === "auth_error" || probe.http_status === 401
          ? (probe.key_sent ? tr("服务拒绝了这个 API Key（401），请检查是否正确或已过期。", "The service rejected this API key (401). Check that it is correct and not expired.")
                            : tr("服务需要认证，请填写 API Key 后重试。", "The service requires authentication. Enter an API key and retry."))
          : tr("服务或模型暂不可用；配置仍可保存，稍后再连接。", "Service or model unavailable. You can save this profile and connect later.")}</p>}
      <div className="settings-actions">
        <Button disabled={busy || !draft.base_url.trim()} onClick={test}><Icon name="refresh" size={15}/>{tr("测试连接", "Test connection")}</Button>
        <Button disabled={busy || !draft.base_url.trim() || invalidNumbers.length > 0} onClick={()=>submit(true)}>{busy ? tr("处理中…", "Working…") : tr("保存并切换", "Save & switch")}</Button>
        <Button disabled={busy || !draft.base_url.trim() || invalidNumbers.length > 0} onClick={()=>submit(false)}>{tr("仅保存", "Save only")}</Button>
        <Button disabled={busy} onClick={()=>setDraft(null)}>{tr("取消", "Cancel")}</Button>
      </div>
    </form>}
    <ConfirmDialog open={confirmDelete !== null} title={tr("删除供应商档案", "Delete provider profile")}
      message={tr("删除后该供应商的端点与密钥将从本机配置中移除，无法撤销。",
        "The endpoint and key are removed from the local configuration. This cannot be undone.")}
      busy={busy} onCancel={()=>setConfirmDelete(null)}
      onConfirm={()=>{const id = confirmDelete; setConfirmDelete(null); if(id) remove(id);}}/>
  </>;
}
