# @localnote/markdown

M2 read-only unified/remark GFM preview with rehype-raw + rehype-sanitize. Rendering never writes source.

Client-side Markdown rendering/preview helpers are implemented here.

- Unknown Obsidian syntax is preserved as safe text; preview never writes source.
- Sanitization remains the security boundary for rendered HTML.
