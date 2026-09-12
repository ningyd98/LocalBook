import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { activateAIProfile, fetchAIProfiles } from "../api/settings";
import type { AIProviderProfile, ServiceSettings } from "../api/settings";

/**
 * Quick provider switch (PLAN-PROVIDERS §5.3).
 *
 * One dropdown that lists the saved providers and applies the picked one. It is
 * deliberately self-contained: the inspector header and the status bar both
 * mount it, and each keeps its own copy of the library so a switch from either
 * place is visible immediately without a page reload.
 */
export function AIProviderSwitch(
  {revision, disabled = false, onSwitched, onManage, variant = "panel"}: {
    /** Settings revision: re-reads the library whenever the shell reloads it. */
    revision: number | undefined;
    disabled?: boolean;
    onSwitched?: (settings: ServiceSettings) => void;
    onManage?: () => void;
    variant?: "panel" | "status";
  },
) {
  const {tr, errorText} = useI18n();
  const [profiles, setProfiles] = useState<AIProviderProfile[]>([]);
  const [activeId, setActiveId] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void fetchAIProfiles()
      .then(list => {if(alive) {
        // Defensive: a response without a library (older server, an unexpected
        // payload) degrades to "no provider yet" instead of crashing the shell.
        setProfiles(Array.isArray(list?.profiles) ? list.profiles : []);
        setActiveId(list?.active_profile_id ?? "");
        setError(null);
      }}, e => {if(alive) setError(errorText(e));});
    return () => {alive = false;};
  }, [revision]);

  const switchTo = async (id: string) => {
    if(!id || id === activeId) return;
    setError(null);
    try {
      const settings = await activateAIProfile(id, revision ?? 0);
      setProfiles(settings.ai.profiles ?? profiles);
      setActiveId(settings.ai.active_profile_id ?? id);
      onSwitched?.(settings);
    } catch (e) {setError(errorText(e));}
  };

  const active = profiles.find(p => p.id === activeId);
  if (profiles.length === 0) {
    return variant === "status"
      ? <button onClick={onManage} title={tr("配置 AI 供应商", "Configure AI providers")}><span className="status-dot warn"/>{tr("AI 供应商", "AI provider")}</button>
      : null;
  }
  return <div className={`provider-switch ${variant}`} title={error ?? tr("切换 AI 供应商", "Switch AI provider")}>
    <label>
      <span className="visually-hidden">{tr("AI 供应商", "AI provider")}</span>
      <select value={activeId} disabled={disabled} aria-label={tr("AI 供应商", "AI provider")}
              onChange={e=>void switchTo(e.target.value)}>
        {profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
      </select>
    </label>
    {variant === "panel" && onManage && <button type="button" className="provider-switch-manage" disabled={disabled}
      onClick={onManage}>{tr("管理供应商…", "Manage providers…")}</button>}
    {variant === "panel" && active && <small>{active.chat_model}</small>}
  </div>;
}
