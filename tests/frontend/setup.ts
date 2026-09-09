/**
 * Vitest setup for LocalNote frontend tests (Phase 0).
 *
 * Loads jest-dom matchers, auto-cleans rendered DOM after each test, and
 * unstubs globals (fetch mocks) so tests never leak into each other.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// CodeMirror measures text ranges during input; jsdom does not implement the
// layout APIs, so provide inert geometry for deterministic lifecycle tests.
if (!Range.prototype.getClientRects) {
  Object.defineProperty(Range.prototype, "getClientRects", { configurable: true, value: () => [] });
}
if (!Range.prototype.getBoundingClientRect) {
  Object.defineProperty(Range.prototype, "getBoundingClientRect", { configurable: true, value: () => new DOMRect() });
}

// jsdom's canvas reports "Not implemented" when getContext is called, which
// makes WebGL detection throw on every graph mount.  A deterministic null
// context (=> no WebGL) turns the fallback path into the default test path;
// tests that need a fake WebGL context re-stub the method themselves.
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;
}

// jsdom's File/Blob do not implement arrayBuffer(); the attachment upload path
// reads the file bytes with it, so provide the standard behaviour.
if (typeof File !== "undefined" && typeof File.prototype.arrayBuffer !== "function") {
  Object.defineProperty(File.prototype, "arrayBuffer", {
    configurable: true,
    writable: true,
    value(this: File) {
      return new Promise<ArrayBuffer>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.onerror = () => reject(reader.error ?? new Error("File read failed"));
        reader.readAsArrayBuffer(this);
      });
    },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
