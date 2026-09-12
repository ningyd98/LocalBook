import { parseWikilink } from "@localnote/protocol";

/** One `[[...]]` occurrence located in the source, without its delimiters. */
interface WikilinkSpan {
  from: number;
  to: number;
  body: string;
}

/**
 * Locate every `[[...]]` / `![[...]]` outside fenced code blocks and inline
 * code, so a rewrite can never touch a literal example in documentation.
 * The scan mirrors `preprocessWikilinks`: a fenced block is tracked per line
 * and an odd number of backticks before the opener marks inline code.
 */
export function wikilinkSpans(source: string): WikilinkSpan[] {
  const spans: WikilinkSpan[] = [];
  let fence: string | null = null;
  let offset = 0;
  for (const line of source.split("\n")) {
    const fenceMatch = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fenceMatch) {
      const marker = fenceMatch[1]![0]!;
      fence = fence && fence === marker ? null : fence === null ? marker : fence;
      offset += line.length + 1;
      continue;
    }
    if (!fence) {
      let cursor = 0;
      while (cursor < line.length) {
        const open = line.indexOf("[[", cursor);
        if (open === -1) break;
        const close = line.indexOf("]]", open + 2);
        if (close === -1) break;
        const ticks = (line.slice(0, open).match(/(?<!`)`(?!`)/g) ?? []).length;
        // `![[x]]` keeps its leading bang so an embed stays an embed.
        const start = open > 0 && line[open - 1] === "!" ? open - 1 : open;
        if (ticks % 2 === 0) {
          spans.push({ from: offset + start, to: offset + close + 2, body: line.slice(start, close + 2) });
        }
        cursor = close + 2;
      }
    }
    offset += line.length + 1;
  }
  return spans;
}

/**
 * Point every link that referenced `oldStem` at `newStem`, returning the new
 * source (or the input when nothing matched).
 *
 * Only a link whose **target stem** is `oldStem` is rewritten, so aliases,
 * sections and block references survive (`[[A|x]]` → `[[B|x]]`), and an
 * ambiguous stem — one that matches several notes in the Vault — is left
 * alone rather than silently re-pointed at the wrong note.
 */
export function rewriteWikilinkTarget(source: string, oldStem: string, newStem: string, ambiguous: boolean): string {
  if (!oldStem || !newStem || ambiguous) return source;
  const from = oldStem.toLowerCase();
  let result = "";
  let cursor = 0;
  let changed = false;
  for (const span of wikilinkSpans(source)) {
    const parsed = parseWikilink(span.body);
    const segments = parsed.target.split("/");
    const target = (segments.at(-1) ?? parsed.target).trim();
    if (parsed.web || !parsed.target || target.toLowerCase() !== from) continue;
    // Keep the authored folder prefix (`[[folder/Old]]` → `[[folder/New]]`) and
    // the alias when there is one; `parseWikilink` falls back to the target.
    const prefix = segments.slice(0, -1).join("/");
    const alias = parsed.label !== parsed.target ? `|${parsed.label}` : "";
    const rebuilt = `${parsed.embed ? "!" : ""}[[${prefix ? `${prefix}/` : ""}${newStem}${parsed.section ? `#${parsed.section}` : ""}${parsed.block ? `^${parsed.block}` : ""}${alias}]]`;
    result += source.slice(cursor, span.from) + rebuilt;
    cursor = span.to;
    changed = true;
  }
  return changed ? result + source.slice(cursor) : source;
}
