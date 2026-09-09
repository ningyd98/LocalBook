import { useRef } from "react";
export function PaneResize({ value, onChange, label, reverse = false, percent = false }: {value: number; onChange: (value: number) => void; label: string; reverse?: boolean; percent?: boolean}) {
  const start = useRef({ x: 0, value: 0, width: 1 });
  return <div className="resize-handle" role="separator" tabIndex={0} aria-label={label} aria-orientation="vertical" aria-valuenow={Math.round(value)} aria-valuemin={percent ? 20 : reverse ? 280 : 200} aria-valuemax={percent ? 80 : reverse ? 480 : 420}
    onPointerDown={event => { start.current = { x: event.clientX, value, width: (percent ? event.currentTarget.closest(".note-surface")?.clientWidth : event.currentTarget.parentElement?.clientWidth) ?? 1 }; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) onChange(start.current.value + (event.clientX - start.current.x) * (reverse ? -1 : 1) * (percent ? 100 / start.current.width : 1)); }}
    onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
    onKeyDown={event => { if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); onChange(value + (event.key === "ArrowRight" ? 1 : -1) * (reverse ? -1 : 1) * (percent ? 2 : 16)); } }}/>
}
