import type { ReactNode } from "react";

export type StatusTone = "ok" | "warn" | "bad" | "info";

export interface StatusCardProps {
  label: string;
  value: string;
  tone?: StatusTone;
  detail?: string | null;
}

const toneClass: Record<StatusTone, string> = {
  ok: "status-ok",
  warn: "status-warn",
  bad: "status-bad",
  info: "status-info",
};

export function StatusCard({ label, value, tone = "info", detail }: StatusCardProps) {
  return (
    <article className={`status-card ${toneClass[tone]}`}>
      <h2 className="status-label">{label}</h2>
      <p className="status-value" data-testid={`status-${label.toLowerCase()}`}>
        {value}
      </p>
      {detail ? <p className="status-detail">{detail}</p> : null}
    </article>
  );
}

export function StatusCards({ children }: { children: ReactNode }) {
  return <section className="status-cards">{children}</section>;
}
