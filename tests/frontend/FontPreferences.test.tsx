/**
 * Font family and global text scale.
 *
 * The scale is a document-level token set (`--font-scale` + `--fs-*`), which is
 * what makes it resize the *whole* interface instead of only the note body; the
 * family is a preference that either re-fonts everything or only note surfaces.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import App from "../../apps/web/src/App";
import { SettingsPanel } from "../../apps/web/src/components/SettingsPanel";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import type { ServiceSettings } from "../../apps/web/src/api/settings";
import { applyFontQuery, fontScaleRange, fontScaleVars, fontStacks, preferenceDefaults, useWorkspaceStore, validatePreferences } from "../../packages/workspace/src";

const store = () => useWorkspaceStore.getState();
const settings = (): ServiceSettings => ({ revision: 1, vault_session_id: "a", changing: false, version: "1.0.0", vault: { root: "/notes/a", status: "ready" }, ai: { enabled: false, base_url: null, chat_model: "auto", active_profile_id: "default", profiles: [], api_key_set: false } } as unknown as ServiceSettings);

beforeEach(() => {
  localStorage.clear();
  store().resetVault();
  store().setPreferences({ fontFamily: "sans", fontScale: 100, fontTarget: "all" });
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.fontTarget;
});

describe("font preferences", () => {
  it("defaults to the system stack at 100%", () => {
    expect(preferenceDefaults.fontFamily).toBe("sans");
    expect(preferenceDefaults.fontScale).toBe(100);
    expect(preferenceDefaults.fontTarget).toBe("all");
    expect(validatePreferences({})).toMatchObject({ fontFamily: "sans", fontScale: 100, fontTarget: "all" });
  });

  it("clamps the scale and rejects unknown families", () => {
    expect(validatePreferences({ fontScale: 9999 }).fontScale).toBe(fontScaleRange.max);
    expect(validatePreferences({ fontScale: 1 }).fontScale).toBe(fontScaleRange.min);
    expect(validatePreferences({ fontScale: Number.NaN }).fontScale).toBe(100);
    expect(validatePreferences({ fontFamily: "comic" as never }).fontFamily).toBe("sans");
    expect(validatePreferences({ fontTarget: "nope" as never }).fontTarget).toBe("all");
  });

  it("scales every size token by the same factor", () => {
    const base = fontScaleVars(100);
    const large = fontScaleVars(150);
    expect(base["--font-scale"]).toBe("1");
    expect(base["--fs-base"]).toBe("13px");
    expect(large["--font-scale"]).toBe("1.5");
    expect(large["--fs-base"]).toBe("19.5px");
    expect(large["--fs-sm"]).toBe("16.5px");
    // Every token moves together: that is what "整体放大缩小" means.
    for (const token of Object.keys(base)) {
      if (token === "--font-scale") continue;
      expect(parseFloat(large[token]!)).toBeGreaterThan(parseFloat(base[token]!));
    }
  });

  it("can be seeded from the URL for sharing a comfortable size", () => {
    expect(applyFontQuery(preferenceDefaults, "?fontScale=130")).toMatchObject({ fontScale: 130 });
    expect(applyFontQuery(preferenceDefaults, "?fontScale=130&fontFamily=serif&fontTarget=note")).toMatchObject({ fontScale: 130, fontFamily: "serif", fontTarget: "note" });
    // Out-of-range values clamp, unknown families are ignored, no query = untouched.
    expect(applyFontQuery(preferenceDefaults, "?fontScale=9999").fontScale).toBe(fontScaleRange.max);
    expect(applyFontQuery(preferenceDefaults, "?fontFamily=comic").fontFamily).toBe("sans");
    expect(applyFontQuery(preferenceDefaults, "")).toEqual(preferenceDefaults);
  });

  it("persists the choice for the next session", () => {
    store().setPreferences({ fontFamily: "serif", fontScale: 130, fontTarget: "note" });
    const saved = JSON.parse(localStorage.getItem("localnote-preferences")!);
    expect(saved).toMatchObject({ fontFamily: "serif", fontScale: 130, fontTarget: "note" });
  });
});

/**
 * The stylesheet is the other half of "整体放大缩小": if any text size stayed a
 * raw pixel value it would ignore the scale and the interface would look mixed.
 * These assertions run against the real stylesheet text.
 */
describe("stylesheet stays fully scalable", () => {
  const stylesheet = readFileSync(resolve(process.cwd(), "../../apps/web/src/styles.css"), "utf8");

  it("has no hard-coded text size left", () => {
    const declarations = stylesheet.match(/font-size:[^;}]+/g) ?? [];
    expect(declarations.length).toBeGreaterThan(100);
    // Tokens, scale-aware calcs and relative `em` all follow the scale; a raw
    // pixel value would not, so any of those is a regression.
    const unscaled = declarations.filter(value => !/var\(--fs-|var\(--font-scale\)|[0-9.]+em/.test(value));
    expect(unscaled).toEqual([]);
  });

  it("scales the text-bearing chrome heights too", () => {
    for (const property of ["height:44px", "height:51px", "height:40px", "height:31px", "min-height:31px"]) {
      expect(stylesheet).not.toContain(property);
    }
    expect(stylesheet).toMatch(/height:calc\(44px \* var\(--font-scale\)\)/);
  });

  it("ships the default tokens so the first paint is already correct", () => {
    expect(stylesheet).toContain("--font-scale:1");
    expect(stylesheet).toContain("--fs-base:13px");
    expect(stylesheet).toContain("--font-note:var(--font-ui)");
  });

  it("drives the editor from the same tokens", () => {
    const editor = readFileSync(resolve(process.cwd(), "../../packages/editor/src/CodeMirrorEditor.tsx"), "utf8");
    expect(editor).toContain("calc(14px * var(--font-scale, 1))");
    expect(editor).toContain("var(--font-note)");
  });
});

describe("document-level scale", () => {
  it("writes the scale tokens and font stacks onto the document root", async () => {
    store().setPreferences({ fontScale: 130, fontFamily: "serif", fontTarget: "all" });
    render(<WorkspaceShell />);
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--font-scale")).toBe("1.3"));
    expect(document.documentElement.style.getPropertyValue("--fs-base")).toBe("16.9px");
    expect(document.documentElement.style.getPropertyValue("--font-ui")).toBe(fontStacks.serif);
    expect(document.documentElement.dataset.fontTarget).toBe("all");
  });

  it("keeps the chrome sans when the family targets notes only", async () => {
    store().setPreferences({ fontFamily: "mono", fontTarget: "note" });
    render(<WorkspaceShell />);
    await waitFor(() => expect(document.documentElement.dataset.fontTarget).toBe("note"));
    expect(document.documentElement.style.getPropertyValue("--font-ui")).toBe(fontStacks.sans);
    // Notes still get the chosen stack.
    expect(document.documentElement.style.getPropertyValue("--font-note")).toBe(fontStacks.mono);
  });

  it("applies the scale from the app shell (status bar control)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(settings()), { status: 200 })));
    render(<App />);
    const zoomIn = await screen.findByRole("button", { name: "Increase text size" });
    await userEvent.click(zoomIn);
    expect(store().fontScale).toBe(105);
    expect(document.documentElement.style.getPropertyValue("--font-scale")).toBe("1.05");

    await userEvent.click(screen.getByRole("button", { name: "Decrease text size" }));
    expect(store().fontScale).toBe(100);

    // The value is also a reset button.
    act(() => store().setPreferences({ fontScale: 145 }));
    await userEvent.click(await screen.findByRole("button", { name: "145%" }));
    expect(store().fontScale).toBe(100);
  });

  it("clamps at both ends of the range", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(settings()), { status: 200 })));
    render(<App />);
    const zoomOut = await screen.findByRole("button", { name: "Decrease text size" });
    act(() => store().setPreferences({ fontScale: fontScaleRange.min }));
    await waitFor(() => expect(zoomOut).toBeDisabled());
    const zoomIn = screen.getByRole("button", { name: "Increase text size" });
    act(() => store().setPreferences({ fontScale: fontScaleRange.max }));
    await waitFor(() => expect(zoomIn).toBeDisabled());
  });

  it("zooms with the keyboard and ctrl+wheel", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(settings()), { status: 200 })));
    render(<App />);
    await screen.findByRole("button", { name: "Increase text size" });
    fireEvent.keyDown(window, { key: "+", ctrlKey: true });
    expect(store().fontScale).toBe(fontScaleRange.step + 100);
    fireEvent.keyDown(window, { key: "-", ctrlKey: true });
    expect(store().fontScale).toBe(100);
    fireEvent.keyDown(window, { key: "0", ctrlKey: true, });
    expect(store().fontScale).toBe(100);
    fireEvent.wheel(window, { deltaY: -100, ctrlKey: true });
    expect(store().fontScale).toBe(100 + fontScaleRange.step);
    fireEvent.wheel(window, { deltaY: 100, ctrlKey: true });
    expect(store().fontScale).toBe(100);
    // A plain wheel is scrolling, not zooming.
    fireEvent.wheel(window, { deltaY: -100 });
    expect(store().fontScale).toBe(100);
  });
});

describe("settings controls", () => {
  it("picks a family, a target and a size", async () => {
    render(<SettingsPanel settings={settings()} onClose={vi.fn()} onSaved={vi.fn()} onSwitch={vi.fn()} initialSection="appearance" />);

    await userEvent.selectOptions(screen.getByLabelText("Font"), "serif");
    expect(store().fontFamily).toBe("serif");

    await userEvent.click(screen.getByRole("button", { name: "Note body only" }));
    expect(store().fontTarget).toBe("note");

    await userEvent.click(screen.getByRole("button", { name: "130%" }));
    expect(store().fontScale).toBe(130);

    fireEvent.change(screen.getByLabelText("Text size"), { target: { value: "115" } });
    expect(store().fontScale).toBe(115);
    expect(within(screen.getByRole("status")).getByText("115%")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(store().fontScale).toBe(100);
  });
});
