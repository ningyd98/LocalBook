import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "@localnote/ui";
import type { IconName } from "@localnote/ui";

/**
 * One entry of a {@link ContextMenu}. `action` items close the menu and run
 * their handler; an item without `onSelect` renders as a static label.
 */
export interface ContextMenuItem {
  id: string;
  label: string;
  icon?: IconName;
  onSelect?: () => void;
  disabled?: boolean;
  /** Visually separate a group (never the first item). */
  separated?: boolean;
}

/**
 * Floating menu anchored to the pointer, shared by every right-click entry
 * point (file tree, tabs, editor, preview).
 *
 * It is positioned `fixed` so it never reflows the surface that opened it,
 * closes on Escape / outside click / scroll, moves the focus to its first item
 * and supports Up/Down/Home/End keyboard navigation.
 */
export function ContextMenu({ x, y, label, items, onClose, children }: { x: number; y: number; label: string; items: ContextMenuItem[]; onClose: () => void; children?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState(() => ({ left: x, top: y }));
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const current = ref.current;
    const rect = current?.getBoundingClientRect();
    // jsdom and other layout-free environments report a zero box: keep the
    // pointer coordinates instead of clamping against a meaningless height.
    if (rect && (rect.width || rect.height)) {
      const margin = 8;
      setPosition({
        left: Math.max(margin, Math.min(x, window.innerWidth - rect.width - margin)),
        top: Math.max(margin, Math.min(y, window.innerHeight - rect.height - margin)),
      });
    } else {
      setPosition({ left: x, top: y });
    }
    current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }, [x, y]);

  useEffect(() => {
    const outside = (event: Event) => { if (!ref.current?.contains(event.target as Node)) closeRef.current(); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeRef.current(); } };
    const scroll = () => closeRef.current();
    document.addEventListener("mousedown", outside, true);
    document.addEventListener("contextmenu", outside, true);
    window.addEventListener("keydown", escape, true);
    window.addEventListener("resize", scroll);
    window.addEventListener("scroll", scroll, true);
    return () => {
      document.removeEventListener("mousedown", outside, true);
      document.removeEventListener("contextmenu", outside, true);
      window.removeEventListener("keydown", escape, true);
      window.removeEventListener("resize", scroll);
      window.removeEventListener("scroll", scroll, true);
    };
  }, []);

  const move = (delta: number) => {
    const buttons = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
    if (!buttons.length) return;
    const index = buttons.findIndex((button) => button === document.activeElement);
    buttons[(index + delta + buttons.length) % buttons.length]?.focus();
  };

  return <div ref={ref} role="menu" aria-label={label} className="context-menu" style={position}
    onKeyDown={event => {
      if (event.key === "ArrowDown") { event.preventDefault(); move(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); move(-1); }
      else if (event.key === "Home") { event.preventDefault(); move(-(ref.current?.querySelectorAll("button").length ?? 1)); }
      else if (event.key === "End") { event.preventDefault(); move(ref.current?.querySelectorAll("button").length ?? 1); }
    }}>
    {items.map(item => <button key={item.id} type="button" role="menuitem" disabled={item.disabled}
      className={item.separated ? "separated" : undefined}
      onClick={() => { closeRef.current(); item.onSelect?.(); }}>
      {item.icon ? <Icon name={item.icon} size={14}/> : <span className="context-menu-spacer"/>}
      <span>{item.label}</span>
    </button>)}
    {children}
  </div>;
}
