import { useMemo } from "react";
import { renderMarkdown } from "./render";
export function Preview({source,className}:{source:string;className?:string}){const html=useMemo(()=>renderMarkdown(source),[source]);return <article className={className} aria-label="Markdown preview" dangerouslySetInnerHTML={{__html:html}}/>;}
