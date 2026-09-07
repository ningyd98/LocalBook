import { CodeMirrorEditor } from "@localnote/editor";
import type { EditorSession } from "@localnote/workspace";
export function EditorPane({session,theme,onChange,onSave}:{session:EditorSession;theme:"light"|"dark";onChange:(v:string)=>void;onSave:()=>void}){if(session.encoding!=="utf8")return <div className="editor-fallback">This file is not valid UTF-8 and is read-only.</div>;return <CodeMirrorEditor value={session.content} theme={theme} onChange={onChange} onSave={onSave} ariaLabel={`Source editor for ${session.path}`}/>}
