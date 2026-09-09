import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeStringify from "rehype-stringify";
import type { Element, Root, RootContent } from "hast";
import { parseWikilink, preprocessWikilinks } from "@localnote/protocol";

/**
 * Markdown render pipeline (PLAN-ATTACHMENTS v1.1 §ATT-14).
 *
 * The remark → rehype-raw → rehype-sanitize → stringify order is unchanged.
 * Two additive pieces support attachment previews:
 *
 * 1. the sanitize schema keeps only safe `img`/`a` URL schemes — `javascript:`,
 *    `data:` and `vbscript:` are removed, as are event attributes and `style`;
 * 2. an optional pure `resolveUrl` callback rewrites an already *sanitized*
 *    Vault-relative reference into a read-only resource URL. It runs after
 *    sanitizing on the parsed HAST, so a dangerous protocol can never be
 *    turned into a fetchable URL and no regex ever rewrites raw HTML.
 */

export interface RenderMarkdownOptions {
  /**
   * Pure resolver called with the relative URL of an `img`/`a` element.
   * Return `null`/`undefined` to leave the URL untouched. It must return an
   * already-encoded URL; it is never given an absolute or unsafe URL.
   */
  resolveUrl?: (url: string, tag: "img" | "a") => string | null | undefined;
}

const SAFE_URL_PROTOCOLS = ["http", "https"];

/**
 * Sanitize schema: the default (GitHub-style) schema plus an explicit `img`
 * attribute allow-list. `width`/`height`/`style`/`onerror` stay forbidden, and
 * `src`/`href` only accept `http`/`https` (relative paths carry no protocol
 * and therefore pass through).
 */
export const markdownSanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    img: [...(defaultSchema.attributes?.img ?? []), "alt", "title"],
  },
  protocols: {
    ...defaultSchema.protocols,
    // ``wikilink:`` is emitted only by preprocessWikilinks below and consumed
    // by the resolver; it never reaches the DOM as a navigable URL.
    href: [...SAFE_URL_PROTOCOLS, "wikilink"],
    src: SAFE_URL_PROTOCOLS,
  },
};

/**
 * True when a sanitized URL is a safe Vault-relative reference that the
 * resolver may rewrite: a relative path or a root-relative path. External
 * absolute URLs, protocol-relative URLs, fragments and any scheme (including
 * ``data:``/``javascript:``, which the sanitizer already removed) are left
 * exactly as authored.
 */
function isSafeRelativeUrl(url: unknown): url is string {
  if (typeof url !== "string" || !url) return false;
  if (url.startsWith("//") || url.startsWith("#")) return false;
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(url)) return false;
  return true;
}

function rewrite(node: RootContent, resolveUrl: NonNullable<RenderMarkdownOptions["resolveUrl"]>): void {
  if (node.type !== "element") return;
  const element = node as Element;
  const tag = element.tagName === "img" ? "img" : element.tagName === "a" ? "a" : null;
  if (tag === "a") {
    const href = element.properties?.["href"];
    if (typeof href === "string" && href.startsWith("wikilink:")) {
      const parsed = parseWikilink(decodeURIComponent(href.slice("wikilink:".length)));
      // hast spells data attributes as ``dataFoo``; the DOM sees ``data-foo``.
      const properties = element.properties as Record<string, unknown>;
      properties["className"] = [...((properties["className"] as string[] | undefined) ?? []), "wikilink"];
      properties["dataWikilink"] = parsed.target;
      if (parsed.section) properties["dataWikilinkSection"] = parsed.section;
      if (parsed.block) properties["dataWikilinkBlock"] = parsed.block;
      if (parsed.embed) properties["dataWikilinkEmbed"] = "true";
      // Not a navigable URL: the target lives in the data attributes only.
      properties["href"] = `#wikilink-${encodeURIComponent(parsed.target)}`;
      return;
    }
  }
  if (tag) {
    const key = tag === "img" ? "src" : "href";
    const current = element.properties?.[key];
    if (isSafeRelativeUrl(current)) {
      let resolved: string | null | undefined;
      try {
        resolved = resolveUrl(current, tag);
      } catch {
        resolved = null;
      }
      if (typeof resolved === "string" && resolved) element.properties[key] = resolved;
    }
  }
  for (const child of element.children ?? []) rewrite(child, resolveUrl);
}

const resolvingProcessor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype, { allowDangerousHtml: true })
  .use(rehypeRaw)
  .use(rehypeSanitize, markdownSanitizeSchema)
  .use(rehypeStringify);

export function renderMarkdown(source: string, options?: RenderMarkdownOptions): string {
  try {
    // The processor always runs so ``[[X]]`` becomes a clickable element; the
    // identity resolver keeps plain rendering byte-identical for other URLs.
    const resolveUrl = options?.resolveUrl ?? ((url: string) => url);
    // ``[[X]]`` becomes an anchor before parsing; the resolver below decides
    // whether it stays a plain link (existing note) or becomes create-able.
    const prepared = preprocessWikilinks(source);
    // ``runSync`` on a shared processor is safe: the resolver is passed as
    // data (never captured in a plugin closure), so concurrent calls cannot
    // observe each other's callback.
    const tree = resolvingProcessor.runSync(resolvingProcessor.parse(prepared)) as Root;
    for (const child of tree.children) rewrite(child, resolveUrl);
    return String(resolvingProcessor.stringify(tree));
  } catch {
    return `<pre>${escapeHtml(source)}</pre>`;
  }
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char] ?? char));
}
