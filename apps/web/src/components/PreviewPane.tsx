import { Preview } from "@localnote/markdown";
export function PreviewPane({source}:{source:string}){return <div className="preview-pane"><Preview source={source}/></div>}
