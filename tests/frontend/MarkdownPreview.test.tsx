import { describe,expect,it } from "vitest";
import { renderMarkdown } from "../../packages/markdown/src/render";
import { resolveVaultRelativePath } from "../../packages/protocol/src";
describe("markdown preview",()=>{it("renders GFM and sanitizes dangerous HTML",()=>{const html=renderMarkdown("# Hi\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n<script>alert(1)</script><a href=\"javascript:alert(1)\">x</a> [[wiki]]");expect(html).toContain("<h1>Hi</h1>");expect(html).toContain("<table>");expect(html).not.toContain("script");expect(html).not.toContain("javascript:");expect(html).toContain("wikilink:wiki")})});

describe("markdown preview attachment resolver (ATT-14)",()=>{
  const resolve=(notePath:string,url:string)=>{const p=resolveVaultRelativePath(notePath,url);return p?`/api/v1/vault/resource?path=${encodeURIComponent(p)}`:null};
  it("rewrites relative and ../ references after sanitizing",()=>{
    const html=renderMarkdown("![a](photo.png)\n\n![b](../attachments/x.png)",{resolveUrl:(url)=>resolve("notes/2026/a.md",url)});
    expect(html).toContain("/api/v1/vault/resource?path=notes%2F2026%2Fphoto.png");
    // ``../`` resolves against the note directory before the URL is built.
    expect(html).toContain("/api/v1/vault/resource?path=notes%2Fattachments%2Fx.png");
  });
  it("decodes authored escapes exactly once",()=>{
    const html=renderMarkdown("![a](photo%20%E5%9B%BE.png)",{resolveUrl:(url)=>resolve("notes/a.md",url)});
    expect(html).toContain("notes%2Fphoto%20%E5%9B%BE.png");
    expect(html).not.toContain("%25");
  });
  it("never rewrites absolute, data, javascript or external URLs",()=>{
    const seen:string[]=[];
    const html=renderMarkdown("[site](https://example.com/a.png)\n\n[bad](javascript:alert(1))\n\n![img](https://example.com/x.png)\n\n![abs](/abs/x.png)",{resolveUrl:(url)=>{seen.push(url);return url.startsWith("http")?"/should/not/happen":"/resolved"}});
    // External links keep their authored href and are never resolved.
    expect(html).toContain('href="https://example.com/a.png"');
    expect(seen).toEqual(["/abs/x.png"]);
    expect(html).not.toContain("javascript:");
    // A root-relative path is already Vault-root-relative and is the only
    // reference handed to the resolver.
    expect(html).toContain('src="/resolved"');
  });
  it("escapes above the vault root instead of resolving",()=>{
    expect(resolveVaultRelativePath("notes/a.md","../../x.png")).toBeNull();
  });
  it("keeps dangerous attributes and protocols out of the output",()=>{
    const html=renderMarkdown('<img src="x.png" onerror="alert(1)" onload="alert(2)" style="width:1px">',{resolveUrl:()=>"/api/v1/vault/resource?path=x.png"});
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("onload");
    expect(html).not.toContain("style=");
  });
});
