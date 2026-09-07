export const DEFAULT_SAVE_DELAY = 800;
export type SaveReason = "auto" | "manual";
/** Debounce boundary for UI input; per-path serialization/coalescing lives in the store. */
export function createDebouncedSave(save: (reason: SaveReason) => void, delay = DEFAULT_SAVE_DELAY) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let reason: SaveReason = "auto";
  return {
    schedule(nextReason: SaveReason = "auto") {
      reason = nextReason === "manual" ? "manual" : reason;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = undefined; const next = reason; reason = "auto"; save(next); }, delay);
    },
    cancel() { if (timer) clearTimeout(timer); timer = undefined; reason = "auto"; },
    flush() { if (!timer) return; clearTimeout(timer); timer = undefined; const next = reason; reason = "auto"; save(next); },
  };
}
