import type { AIChatRequest, AIChatResponse, AIClassifyRequest, AIClassifyResponse, AIExtractTodosRequest, AIExtractTodosResponse, AIRelatedRequest, AIRelatedResponse, AISummarizeRequest, AISummarizeResponse, AIStatusResponse, AITagsRequest, AITagsResponse, BacklinksResponse, FileMutationResponse, FileReadResponse, GraphQuery, GraphResponse, HealthResponse, IndexRebuildResponse, NoteLinksResponse, NoteMetadataResponse, SearchResponse, VaultErrorBody, VaultFileTreeResponse } from "./types";
const API_BASE="/api/v1";
/** Server meta attached to AI structured errors (S2): prompt_version/model. */
export interface ApiErrorMeta { prompt_version?: string; model?: string; }
export class ApiError<T = unknown> extends Error {
  declare readonly __data?: T;
  readonly status: number;
  readonly code: string | undefined;
  readonly path: string | null | undefined;
  readonly meta: ApiErrorMeta | undefined;
  constructor(message:string,status:number,code?:string,path?:string|null,meta?:ApiErrorMeta){super(message);this.name="ApiError";this.status=status;this.code=code;this.path=path;this.meta=meta}
}
let vaultSession: string | null = null;
export const setVaultSession = (session: string | null) => { vaultSession = session; };
export const getVaultSession = () => vaultSession;
export async function request<T>(path:string,init?:RequestInit):Promise<T>{
  const scoped = !path.startsWith("/settings") && path !== "/health" && path !== "/ai/status";
  const session = vaultSession;
  const headers = new Headers(init?.headers);
  if (scoped && session) headers.set("X-LocalNote-Vault-Session", session);
  let response:Response;
  try{response=await fetch(`${API_BASE}${path}`,{ ...init, headers })}catch(error){throw new ApiError(error instanceof Error?error.message:"Network request failed",0,"network_error")}
  let body:unknown=null;
  try{body=await response.json()}catch{/* non-JSON error bodies still surface as a safe ApiError below */}
  if(scoped && session !== vaultSession) throw new ApiError("Response belongs to a previous vault.",409,"stale_response");
  if(!response.ok){
    const errorBody=body as Partial<VaultErrorBody> & { meta?: ApiErrorMeta };
    const error=errorBody?.error;
    const meta=errorBody?.meta&&typeof errorBody.meta==="object"?errorBody.meta:undefined;
    if (scoped && ["vault_session_changed", "vault_session_required"].includes(error?.code ?? "")) {
      window.dispatchEvent(new Event("localnote-vault-changed"));
    }
    throw new ApiError(typeof error?.message==="string"?error.message:`Request failed (${response.status})`,response.status,typeof error?.code==="string"?error.code:undefined,typeof error?.path==="string"?error.path:null,meta)
  }
  return body as T
}
export function fetchHealth(){return request<HealthResponse>("/health")}
export function fetchAIStatus(){return request<AIStatusResponse>("/ai/status")}
export function fetchVaultFiles(options:{recursive?:boolean;includeHidden?:boolean}={}){const query=new URLSearchParams({recursive:String(options.recursive??true),include_hidden:String(options.includeHidden??false)});return request<VaultFileTreeResponse>(`/vault/files?${query}`)}
export function fetchVaultFile(path:string){return request<FileReadResponse>(`/vault/file?${new URLSearchParams({path})}`)}
export function patchVaultFile(args:{path:string;contentBase64:string;expectedSha256:string}){return request<FileMutationResponse>("/vault/file",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:args.path,content_base64:args.contentBase64,expected_sha256:args.expectedSha256})})}
/** Create a new folder. The parent directory must already exist (server rule). */
export function createVaultDirectory(args:{path:string}){return request<FileMutationResponse>("/vault/directory",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:args.path})})}
/** Move or rename a file (drag & drop, rename). Never overwrites the target. */
export function moveVaultFile(args:{sourcePath:string;destinationPath:string;expectedSha256?:string|null}){return request<FileMutationResponse>("/vault/file/move",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_path:args.sourcePath,destination_path:args.destinationPath,expected_sha256:args.expectedSha256??null})})}
/** Create a new note. The parent directory must already exist (server rule). */
export function createVaultFile(args:{path:string;contentBase64:string}){return request<FileMutationResponse>("/vault/file",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:args.path,content_base64:args.contentBase64})})}
/**
 * JSON/base64 attachment upload (small files, ≤10 MiB).
 * `targetDirectory` is decided by the caller's entry point; `""` is the Vault
 * root. The server never infers the current note directory.
 */
export function uploadAttachmentBase64(args:{originalName:string;contentBase64:string;targetDirectory:string}){return request<import("./types").AttachmentUploadResponse>("/vault/attachments",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({original_name:args.originalName,content_base64:args.contentBase64,target_directory:args.targetDirectory})})}
/**
 * Streaming multipart attachment upload (large files). The browser sets the
 * multipart boundary itself, so no Content-Type header is passed here.
 */
export function uploadAttachmentMultipart(file:File,targetDirectory:string,originalName?:string){const form=new FormData();form.append("file",file,file.name||"attachment");form.append("target_directory",targetDirectory);if(originalName)form.append("original_name",originalName);return request<import("./types").AttachmentUploadResponse>("/vault/attachments/multipart",{method:"POST",body:form})}
/** Encoded read-only URL for a Vault-root-relative resource path. */
export function vaultResourceUrl(path:string){return `${API_BASE}/vault/resource?${new URLSearchParams({path})}`}
export {resolveVaultRelativePath} from "@localnote/protocol";
/** Encode each path segment (spaces/Chinese/Emoji) while keeping `/` literal. */
function encodeNotePath(path:string){return path.split("/").map(encodeURIComponent).join("/")}
function graphQueryString(query:GraphQuery):string{const params=new URLSearchParams();if(query.limit!==undefined)params.set("limit",String(query.limit));if(query.offset!==undefined)params.set("offset",String(query.offset));if(query.tag!==undefined&&query.tag!==null)params.set("tag",query.tag);if(query.include_broken!==undefined)params.set("include_broken",String(query.include_broken));if(query.depth!==undefined)params.set("depth",String(query.depth));if(query.direction!==undefined)params.set("direction",query.direction);const serialized=params.toString();return serialized?`?${serialized}`:""}
export function fetchGraph(query:GraphQuery={}){return request<GraphResponse>(`/graph${graphQueryString(query)}`)}
export function fetchLocalGraph(note:string,query:GraphQuery={}){return request<GraphResponse>(`/graph/local/${encodeNotePath(note)}${graphQueryString(query)}`)}
export function fetchTagGraph(tag:string,query:GraphQuery={}){return request<GraphResponse>(`/graph/tag/${encodeNotePath(tag)}${graphQueryString(query)}`)}
export function fetchMetadata(path:string){return request<NoteMetadataResponse>(`/metadata/${encodeNotePath(path)}`)}
export function fetchLinks(path:string){return request<NoteLinksResponse>(`/links/${encodeNotePath(path)}`)}
export function fetchBacklinks(path:string){return request<BacklinksResponse>(`/backlinks/${encodeNotePath(path)}`)}
export function searchNotes(query:string){return request<SearchResponse>(`/search?${new URLSearchParams({q:query})}`)}
export function rebuildIndex(){return request<IndexRebuildResponse>("/index/rebuild",{method:"POST"})}
function postAI<T>(operation:string, body:unknown){return request<T>(`/ai/${operation}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
export function aiChat(args:AIChatRequest){return postAI<AIChatResponse>("chat",args)}
export function aiSummarize(args:AISummarizeRequest){return postAI<AISummarizeResponse>("summarize",args)}
export function aiTags(args:AITagsRequest){return postAI<AITagsResponse>("tags",args)}
export function aiRelated(args:AIRelatedRequest){return postAI<AIRelatedResponse>("related",args)}
export function aiExtractTodos(args:AIExtractTodosRequest){return postAI<AIExtractTodosResponse>("extract_todos",args)}
export function aiClassify(args:AIClassifyRequest){return postAI<AIClassifyResponse>("classify",args)}
export const fetchAIChat = aiChat;
export const fetchAISummarize = aiSummarize;
export const fetchAITags = aiTags;
export const fetchAIRelated = aiRelated;
export const fetchAIExtractTodos = aiExtractTodos;
export const fetchAIClassify = aiClassify;
export function listHistory(){return request<import("./types").HistoryPageDTO>("/history")}
export function getHistory(id:string){return request<import("./types").JobDetailDTO>(`/history/${encodeURIComponent(id)}`)}
export function createJob(body:import("./types").JobCreateRequest){return request<import("./types").JobDetailDTO>("/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
export function acceptJob(id:string,body:import("./types").JobActionRequest={confirm:true}){return request<import("./types").JobDetailDTO>(`/jobs/${encodeURIComponent(id)}/accept`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
export function rejectJob(id:string){return request<import("./types").JobDetailDTO>(`/jobs/${encodeURIComponent(id)}/reject`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirm:true})})}
export function undoHistory(id:string){return request<import("./types").UndoResponseDTO>(`/history/${encodeURIComponent(id)}/undo`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirm:true})})}
export function fetchSchedulerStatus(){return request<import("./types").SchedulerStatusResponse>("/scheduler/status")}
export function runSchedulerTask(task:string,body:import("./types").SchedulerRunRequest={confirm:false}){return request<import("./types").SchedulerRunDTO>(`/scheduler/run/${encodeURIComponent(task)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
export function listSchedulerRuns(params:{limit?:number;offset?:number;task?:string}={}){const query=new URLSearchParams();if(params.limit!==undefined)query.set("limit",String(params.limit));if(params.offset!==undefined)query.set("offset",String(params.offset));if(params.task)query.set("task",params.task);const serialized=query.toString();return request<import("./types").SchedulerRunsPageDTO>(`/scheduler/runs${serialized?`?${serialized}`:""}`)}
export function schedulerRecovery(runId:string,action:import("./types").SchedulerRecoveryAction){return request<Record<string,unknown>>(`/scheduler/recovery/${encodeURIComponent(runId)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})})}
