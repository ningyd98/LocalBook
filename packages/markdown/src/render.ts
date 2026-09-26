import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeStringify from "rehype-stringify";
import type { Element, ElementContent, Root, RootContent } from "hast";
import { markTaskCheckboxes, parseWikilink, preprocessHighlights, preprocessWikilinks } from "@localnote/protocol";

/**
 * Markdown render pipeline (PLAN-ATTACHMENTS v1.1 §ATT-14).
 *
 * The remark → rehype-raw → rehype-sanitize → stringify order is unchanged.
 * Three additive pieces support attachment previews and inline formatting:
 *
 * 1. the sanitize schema keeps only safe `img`/`a` URL schemes — `javascript:`,
 *    `data:` and `vbscript:` are removed, as are event attributes and `style`
 *    (except the single colour declaration the editor's colour command emits);
 * 2. an optional pure `resolveUrl` callback rewrites an already *sanitized*
 *    Vault-relative reference into a read-only resource URL. It runs after
 *    sanitizing on the parsed HAST, so a dangerous protocol can never be
 *    turned into a fetchable URL and no regex ever rewrites raw HTML.
 * 3. `==highlight==` is preprocessed into `<mark>` before parsing (see
 *    {@link preprocessHighlights}); `mark` is allowed by the schema below.
 */

export interface RenderMarkdownOptions {
  /**
   * Pure resolver called with the relative URL of an `img`/`a` element.
   * Return `null`/`undefined` to leave the URL untouched. It must return an
   * already-encoded URL; it is never given an absolute or unsafe URL.
   */
  resolveUrl?: (url: string, tag: "img" | "a") => string | null | undefined;
  /** Build a PDF preview URL for a safe Vault-relative document reference. */
  resolveDocumentUrl?: (url: string) => string | null | undefined;
  /** Add a delegated “Transcribe” affordance to rendered audio attachments. */
  enableAudioTranscription?: boolean;
}

const SAFE_URL_PROTOCOLS = ["http", "https"];

/**
 * The only `style` a note may carry: a bare `color:#rgb`/`#rrggbb`
 * declaration. `background:url(…)`, `position:fixed` and every other
 * declaration — the ones that turn a note into an exfiltration or overlay
 * vector — never match, so they are dropped by the sanitizer.
 */
const COLOR_STYLE = /^color:\s*#[0-9a-fA-F]{3,8}$/;

/** hast-util-sanitize attribute rule: property name plus its allowed values. */
const COLOR_STYLE_ATTRIBUTE: [string, RegExp] = ["style", COLOR_STYLE];

/**
 * Sanitize schema: the default (GitHub-style) schema plus an explicit `img`
 * attribute allow-list and the `<mark>` tag. `width`/`height`/`onerror` stay
 * forbidden, `src`/`href` only accept `http`/`https` (relative paths carry no
 * protocol and therefore pass through), and `span` may only style a colour.
 */
export const markdownSanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "mark"],
  attributes: {
    ...defaultSchema.attributes,
    img: [...(defaultSchema.attributes?.img ?? []), "alt", "title"],
    // Task-list toggles are interactive checkbox spans; preserve only their
    // semantic role, keyboard focus, state and task metadata. The colour span
    // adds the single constrained `style` value.
    span: [...(defaultSchema.attributes?.span ?? []), "className", "dataTaskIndex", "dataTaskChecked", "role", "tabIndex", "ariaChecked", COLOR_STYLE_ATTRIBUTE],
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

function isAudioReference(url: string): boolean {
  const withoutFragment = url.split("#", 1)[0] ?? url;
  const withoutQuery = withoutFragment.split("?", 1)[0] ?? withoutFragment;
  return /\.(mp3|wav|m4a|aac|flac|ogg|oga|opus|webm|amr|caf|aiff?|wma)$/i.test(withoutQuery);
}

function isDocumentReference(url: string): boolean {
  const withoutFragment = url.split("#", 1)[0] ?? url;
  const withoutQuery = withoutFragment.split("?", 1)[0] ?? withoutFragment;
  return /\.(pdf|doc|docx|docm|dotx|dotm|wps|wpt|ppt|pptx|pptm|pps|ppsx|potx|potm|dps|dpt|xls|xlsx|xlsm|et|ett|odt|ods|odp|ott|otp|ots|rtf|csv)$/i.test(withoutQuery);
}

function isSafeResolvedUrl(url: string): boolean {
  return isSafeRelativeUrl(url);
}

function textContent(node: Element): string {
  return (node.children ?? []).map(child => {
    if (child.type === "text") return child.value;
    return child.type === "element" ? textContent(child as Element) : "";
  }).join("").trim();
}

function documentChildren(
  reference: string,
  previewUrl: string,
  downloadUrl: string,
  title: string,
): ElementContent[] {
  return [
    {
      type: "element",
      tagName: "iframe",
      properties: {
        src: previewUrl,
        title: title || reference,
        loading: "lazy",
      },
      children: [],
    } as ElementContent,
    {
      type: "element",
      tagName: "a",
      properties: {
        href: downloadUrl,
        className: ["document-attachment-download"],
        download: true,
        target: "_blank",
        rel: ["noopener", "noreferrer"],
      },
      children: [{ type: "text", value: "打开 / 下载原文件 · Open / download" }],
    } as ElementContent,
  ];
}

function audioChildren(reference: string, resolved: string, enableTranscription: boolean): ElementContent[] {
  const children: ElementContent[] = [{
    type: "element",
    tagName: "audio",
    properties: { controls: true, preload: "metadata", src: resolved },
    children: [],
  } as ElementContent];
  if (enableTranscription) {
    children.push({
      type: "element",
      tagName: "button",
      properties: {
        type: "button",
        className: ["audio-transcribe"],
        dataAudioReference: reference,
      },
      children: [{ type: "text", value: "转文字 / Transcribe" }],
    } as ElementContent);
  }
  return children;
}

function rewrite(
  node: RootContent,
  options: {
    resolveUrl: NonNullable<RenderMarkdownOptions["resolveUrl"]>;
    resolveDocumentUrl?: RenderMarkdownOptions["resolveDocumentUrl"];
    enableAudioTranscription: boolean;
  },
): void {
  const { resolveUrl, resolveDocumentUrl, enableAudioTranscription } = options;
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
      if (typeof resolved === "string" && resolved) {
        if (isAudioReference(current)) {
          // Audio uses a deliberately generated wrapper after sanitization so
          // raw HTML cannot smuggle arbitrary media controls into a note.
          element.tagName = "span";
          element.properties = { className: ["audio-attachment"] };
          element.children = audioChildren(current, resolved, enableAudioTranscription);
          return;
        }
        if (isDocumentReference(current) && resolveDocumentUrl) {
          let previewUrl: string | null | undefined;
          try {
            previewUrl = resolveDocumentUrl(current);
          } catch {
            previewUrl = null;
          }
          if (typeof previewUrl === "string" && isSafeResolvedUrl(previewUrl)) {
            const title = tag === "a"
              ? textContent(element)
              : String(element.properties?.["alt"] ?? "");
            element.tagName = "span";
            element.properties = {
              className: ["document-attachment"],
              dataDocumentReference: current,
            };
            const downloadUrl = isSafeResolvedUrl(resolved) ? resolved : current;
            element.children = documentChildren(current, previewUrl, downloadUrl, title);
            return;
          }
        }
        element.properties[key] = resolved;
      }
    }
  }
  for (const child of element.children ?? []) rewrite(child, options);
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
    // ``==X==`` becomes ``<mark>X</mark>`` in the same pre-parse pass.
    const prepared = preprocessWikilinks(markTaskCheckboxes(preprocessHighlights(source)));
    // ``runSync`` on a shared processor is safe: the resolver is passed as
    // data (never captured in a plugin closure), so concurrent calls cannot
    // observe each other's callback.
    const tree = resolvingProcessor.runSync(resolvingProcessor.parse(prepared)) as Root;
    for (const child of tree.children) rewrite(child, {
      resolveUrl,
      resolveDocumentUrl: options?.resolveDocumentUrl,
      enableAudioTranscription: Boolean(options?.enableAudioTranscription),
    });
    return String(resolvingProcessor.stringify(tree));
  } catch {
    return `<pre>${escapeHtml(source)}</pre>`;
  }
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char] ?? char));
}
