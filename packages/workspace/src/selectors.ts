export function isEditableMarkdown(path:string){return /\.(md|markdown)$/i.test(path)&&!path.split("/").some((part)=>part.startsWith("."));}
