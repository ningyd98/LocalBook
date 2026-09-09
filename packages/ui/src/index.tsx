import type { ButtonHTMLAttributes, HTMLAttributes, PropsWithChildren } from "react";
import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
const cx=(...v:Array<string|false|undefined>)=>v.filter(Boolean).join(" ");
export function Button({className,...props}:ButtonHTMLAttributes<HTMLButtonElement>){return <button className={cx("ui-button",className)} {...props}/>}
export function IconButton({className,...props}:ButtonHTMLAttributes<HTMLButtonElement>){return <button className={cx("ui-icon-button",className)} {...props}/>}
export function Badge({children,className}:{children:ReactNode;className?:string}){return <span className={cx("ui-badge",className)}>{children}</span>}
export function Panel({children,className,...props}:PropsWithChildren<HTMLAttributes<HTMLElement>>){return <section className={cx("ui-panel",className)} {...props}>{children}</section>}
export function EmptyState({title,children}:{title:string;children?:ReactNode}){return <div className="ui-empty"><strong>{title}</strong>{children&&<span>{children}</span>}</div>}
export function StatusBanner({children,className}:{children:ReactNode;className?:string}){return <div role="status" className={cx("ui-status",className)}>{children}</div>}
export function TreeRow({children,className,...props}:PropsWithChildren<HTMLAttributes<HTMLDivElement>>){return <div role="treeitem" className={cx("ui-tree-row",className)} {...props}>{children}</div>}
export function Tab({children,...props}:PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>>){return <button role="tab" className="ui-tab" {...props}>{children}</button>}

export type IconName = "files" | "search" | "graph" | "ai" | "history" | "settings" | "close" | "chevron" | "folder" | "note" | "panelLeft" | "panelRight" | "refresh" | "edit" | "preview" | "split" | "link" | "check" | "arrow" | "sun" | "moon" | "paperclip" | "image";
const paths: Record<IconName, ReactNode> = {
  files: <><path d="M5 3h9l5 5v13H5z"/><path d="M14 3v6h5M8 13h8M8 17h6"/></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
  graph: <><circle cx="5" cy="7" r="3"/><circle cx="18" cy="5" r="3"/><circle cx="14" cy="19" r="3"/><path d="m8 7 7-2M7 10l5 6M17 8l-2 8"/></>,
  ai: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4M18 4h4"/></>,
  history: <><path d="M3 11a9 9 0 1 1 3 8M3 4v7h7"/><path d="M12 7v6l4 2"/></>,
  settings: <><path d="m10 3-1 3-3 1-3-1-1 4 3 2 1 3-1 3 4 2 2-2h3l3 2 3-3-2-3v-3l2-2-2-4-3 1-3-1-1-3z"/><circle cx="11" cy="12" r="3"/></>,
  close: <path d="m6 6 12 12M6 18 18 6"/>, chevron: <path d="m9 5 7 7-7 7"/>,
  folder: <path d="M3 6h6l2 2h10v12H3z"/>, note: <><path d="M5 3h10l4 4v14H5zM15 3v5h4M8 12h8M8 16h6"/></>,
  panelLeft: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></>,
  panelRight: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5M20 12a8 8 0 0 0-14-6M4 12a8 8 0 0 0 14 6"/></>,
  edit: <><path d="m4 16 12-12 4 4L8 20H4zM13 7l4 4"/></>,
  preview: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12"/><circle cx="12" cy="12" r="3"/></>,
  split: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/></>,
  link: <><path d="m10 14 4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0) scale(.9 1)"/></>,
  check: <path d="m5 12 4 4L19 6"/>, arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1"/></>,
  moon: <path d="M20 15A9 9 0 0 1 9 3a9 9 0 1 0 11 12"/>,
  paperclip: <path d="M20 11.5 12 19.5a5 5 0 0 1-7-7l8-8a3.5 3.5 0 0 1 5 5l-8 8a2 2 0 0 1-3-3l7-7"/>, image: <><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="m4 18 5-5 4 4 3-3 4 4"/></>,
};
export function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>{paths[name]}</svg>;
}

export function Dialog({ children, onClose, label, className = "", busy = false }: PropsWithChildren<{onClose: () => void; label: string; className?: string; busy?: boolean}>) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  const busyRef = useRef(busy); busyRef.current = busy;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const root = ref.current;
    const focusable = () => Array.from(root?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex="0"]') ?? []).filter(e => !e.closest('[hidden]'));
    (focusable()[0] ?? root)?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) { event.preventDefault(); event.stopPropagation(); closeRef.current(); }
      if (event.key === "Tab") {
        const items = focusable(); const first = items[0], last = items.at(-1);
        if (!first) { event.preventDefault(); root?.focus(); }
        else if (event.shiftKey && (document.activeElement === first || document.activeElement === root)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    root?.addEventListener("keydown", key);
    return () => { root?.removeEventListener("keydown", key); previous?.focus(); };
  }, []);
  return <div className="dialog-backdrop" onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}><section ref={ref} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} className={`dialog ${className}`}>{children}</section></div>;
}
