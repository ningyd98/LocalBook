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
  ai_disabled: "AI 已关闭，可在设置中启用。",
  ai_not_configured: "尚未配置 AI 服务，请前往设置。",
  ai_model_not_found: "未找到可用的对话模型，请检查模型 ID。",
  network_error: "连接中断，请检查本地服务后重试。",
  not_found: "未找到该文件，可能已被移动或删除。",
  // 附件上传：错误码来自服务端稳定契约，不拼接服务端 message，也不展示绝对路径
  invalid_request: "请求无效，请检查文件名或目标目录。",
  invalid_attachment_name: "文件名无效，请重命名后重试。",
  file_too_large: "文件超过大小限制，无法上传。",
  already_exists: "同名文件已存在，未覆盖原文件。",
  path_traversal: "目标路径不安全，已拒绝上传。",
  symlink_escape: "目标目录是符号链接，已拒绝上传。",
  invalid_name: "文件名无效，请检查后重试。",
};
export function localizedError(error: unknown, locale: string): string {
  const value = error as { code?: string; message?: string } | null;
  if (locale === "en-US") return value?.message ?? "Operation failed. Please retry.";
  return messages[value?.code ?? ""] ?? (value?.message && /[\u4e00-\u9fff]/.test(value.message) ? value.message : "操作未完成，请检查连接或稍后重试。");
}
