import {useI18n} from "../i18n";
import { Button } from "@localnote/ui";
import type { JobCreateRequest } from "@localnote/protocol";
export function OrganizerActions({ onCreate, disabled = false }: { onCreate: (request: JobCreateRequest) => void; disabled?: boolean }) { const {tr}=useI18n(); const run = (task_type: "daily_organizer" | "weekly_review") => onCreate({ task_type, permission_level: 1, scope: { paths: [], max_files: 10 }, execute: false }); return <div className="organizer-actions"><Button disabled={disabled} onClick={() => run("daily_organizer")}>{tr("生成每日整理建议","Daily Organizer")}</Button><Button disabled={disabled} onClick={() => run("weekly_review")}>{tr("生成每周回顾建议","Weekly Review")}</Button></div>; }
