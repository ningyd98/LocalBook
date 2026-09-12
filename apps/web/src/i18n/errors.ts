const messages: Record<string, string> = {
  http_error: "AI 服务未接受连接，请检查服务的认证与访问配置。",
  timeout: "AI 服务响应超时，请稍后重试。",
  not_configured: "尚未配置 AI 服务地址。",
  no_matching_model: "服务已连接，但当前配置的对话模型不可用。",
  unsupported_action: "当前文件格式无法安全应用此变更，请在编辑器中手动处理。",
  runtime_busy: "仍有请求或任务正在运行，请稍后重试。",
  workspace_switching: "工作空间正在切换，请稍后重试。",
  settings_conflict: "设置已在其他页面更新，请重新打开设置以载入最新配置。",
  settings_local_only: "设置只能通过受信任的本机页面管理。",
  invalid_vault_root: "请选择已存在、可访问的绝对目录路径，配置文件须位于笔记库之外。",
  vault_prepare_failed: "新笔记库或索引无法初始化，原工作空间已保留。",
  vault_switch_failed: "切换失败，原笔记库与配置已保留。",
  settings_apply_failed: "无法保存配置，原设置已保留。",
  invalid_ai_endpoint: "请填写不含账号、密码或查询参数的 HTTP(S) 服务地址。",
  vault_session_changed: "笔记库已在其他页面切换，草稿已保留，保存已暂停。",
  vault_session_required: "页面需要更新，草稿已保留，请重新连接笔记库。",
  vault_not_configured: "尚未连接笔记库，请在设置中选择本地文件夹。",
  vault_unavailable: "笔记库暂不可用，请检查目录与访问权限。",
  index_unavailable: "索引暂不可用，可在系统设置中重建索引。",
  file_conflict: "文件已被其他程序修改，请先处理版本冲突。",
  atomic_write_failed: "保存未成功，内容仍保留在编辑器中，请重试。",
  ai_unavailable: "AI 服务暂不可用，请检查连接设置。",
  ai_timeout: "AI 生成超时：本地模型较慢，可调大服务端 LOCALNOTE_AI__REQUEST_TIMEOUT_SECONDS。",
  ai_disabled: "AI 已关闭，可在设置中启用。",
  ai_not_configured: "尚未配置 AI 服务，请前往设置。",
  ai_model_not_found: "未找到可用的对话模型，请检查模型 ID。",
  // AI 供应商档案（PLAN-PROVIDERS）：稳定错误码，不拼接服务端 message
  ai_profile_not_found: "该 AI 供应商档案已不存在，请重新打开设置载入最新配置。",
  ai_profile_active: "正在使用的供应商无法删除，请先切换到其他供应商。",
  ai_profile_last: "至少需要保留一个 AI 供应商档案。",
  ai_profiles_full: "供应商档案数量已达上限（30 个），请先删除不再使用的档案。",
  network_error: "连接中断，请检查本地服务后重试。",
  not_found: "未找到该文件，可能已被移动或删除。",
  // 附件上传：错误码来自服务端稳定契约，不拼接服务端 message，也不展示绝对路径
  // 通用 400/422：这句话要能描述设置类请求，不能只提附件（附件有自己的错误码）
  invalid_request: "请求内容无效，请检查填写的字段后重试。",
  invalid_attachment_name: "文件名无效，请重命名后重试。",
  file_too_large: "文件超过大小限制，无法上传。",
  already_exists: "同名文件已存在，未覆盖原文件。",
  path_traversal: "目标路径不安全，已拒绝上传。",
  symlink_escape: "目标目录是符号链接，已拒绝上传。",
  invalid_name: "文件名无效，请检查后重试。",
};
export function localizedError(error: unknown, locale: string): string {
  const value = error as { code?: string; message?: string; status?: number; endpoint?: string } | null;
  // Locale behaviour is unchanged on purpose: English keeps the server message
  // (which the API writes in English) and Chinese prefers the curated map. New
  // provider codes are mapped below so zh-CN never shows a raw error code.
  const detail = diagnostic(value);
  if (locale === "en-US") return value?.message ? `${value.message}${detail}` : `Operation failed. Please retry.${detail}`;
  const curated = messages[value?.code ?? ""];
  if (curated) return curated;
  if (value?.message && /[\u4e00-\u9fff]/.test(value.message)) return value.message;
  // No curated code and no server sentence: say what actually failed instead of
  // a dead-end "please retry" — an unknown endpoint (HTTP 404) is the most
  // common real cause when the backend is older than the page.
  return `操作未完成：${value?.message || "请求失败"}${detail}`;
}

/**
 * `（HTTP 404 · /settings/ai/profiles）`-style suffix, or an empty string when
 * there is nothing concrete to add. Never includes a vault path or a secret.
 */
function diagnostic(value: { status?: number; endpoint?: string } | null): string {
  const parts: string[] = [];
  if (typeof value?.status === "number" && value.status > 0) parts.push(`HTTP ${value.status}`);
  if (value?.endpoint) parts.push(value.endpoint);
  return parts.length ? `（${parts.join(" · ")}）` : "";
}
