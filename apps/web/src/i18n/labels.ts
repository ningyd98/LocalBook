import { useI18n } from "./index";
const names: Record<string, [string, string]> = {
  daily_organizer: ["每日整理", "Daily organizer"], weekly_review: ["每周回顾", "Weekly review"],
  index_consistency: ["索引一致性检查", "Index consistency"], manual: ["手动操作", "Manual changes"],
  queued: ["排队中", "Queued"], running: ["运行中", "Running"], previewed: ["预览已生成", "Preview ready"],
  awaiting_confirmation: ["等待确认", "Awaiting confirmation"], committed: ["已应用", "Applied"],
  rejected: ["已拒绝", "Rejected"], undone: ["已撤销", "Undone"], failed: ["失败", "Failed"],
  timed_out: ["已超时", "Timed out"], recovery_required: ["需要恢复", "Recovery needed"],
  conflict: ["存在冲突", "Conflict"], rolled_back: ["已回滚", "Rolled back"], rollback_failed: ["回滚失败", "Rollback failed"],
  skipped_duplicate: ["跳过重复任务", "Duplicate skipped"], proposed: ["建议变更", "Proposed"], pending: ["待处理", "Pending"],
  accepted: ["已接受", "Accepted"], create: ["创建", "create"], update: ["更新", "update"], move: ["移动", "move"], delete: ["删除", "delete"],
};
export function useTaskLabels() {
  const { locale } = useI18n();
  return (value: string) => names[value]?.[locale === "zh-CN" ? 0 : 1] ?? value;
}
export function formatTaskTime(value: string, locale: string, timeZone?: string) {
  try { return new Intl.DateTimeFormat(locale, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(value)); }
  catch { return value; }
}
