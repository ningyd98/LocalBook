import { EmptyState } from "@localnote/ui";
export function WorkspaceStatus({message}:{message:string}){return <EmptyState title="Workspace unavailable">{message}</EmptyState>}
