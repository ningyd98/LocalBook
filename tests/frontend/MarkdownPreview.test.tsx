import { describe,expect,it } from "vitest";
import { renderMarkdown } from "../../packages/markdown/src/render";
describe("markdown preview",()=>{it("renders GFM and sanitizes dangerous HTML",()=>{const html=renderMarkdown("# Hi\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n<script>alert(1)</script><a href=\"javascript:alert(1)\">x</a> [[wiki]]");expect(html).toContain("<h1>Hi</h1>");expect(html).toContain("<table>");expect(html).not.toContain("script");expect(html).not.toContain("javascript:");expect(html).toContain("[[wiki]]")})});
