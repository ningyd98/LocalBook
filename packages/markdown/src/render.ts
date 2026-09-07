import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import rehypeStringify from "rehype-stringify";

const processor=unified().use(remarkParse).use(remarkGfm).use(remarkRehype,{allowDangerousHtml:true}).use(rehypeRaw).use(rehypeSanitize).use(rehypeStringify);
export function renderMarkdown(source:string):string { try { return String(processor.processSync(source)); } catch { return `<pre>${escapeHtml(source)}</pre>`; } }
function escapeHtml(value:string){return value.replace(/[&<>\"']/g,(char)=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]??char));}
