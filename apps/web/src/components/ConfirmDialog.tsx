import { Button, Dialog } from "@localnote/ui";
import { useI18n } from "../i18n";
interface ConfirmDialogProps {open:boolean;title?:string;message:string;onConfirm:()=>void;onCancel:()=>void;busy?:boolean;}
export function ConfirmDialog({open,title,message,onConfirm,onCancel,busy=false}:ConfirmDialogProps){const{t,tr}=useI18n();if(!open)return null;return <Dialog label={title??tr("确认操作","Confirm changes")} onClose={onCancel} busy={busy} className="confirm-dialog"><h2>{title??tr("确认操作","Confirm changes")}</h2><p>{message}</p><div className="confirm-actions"><Button disabled={busy} onClick={onCancel}>{t.buttons.cancel}</Button><Button className="primary" disabled={busy} onClick={onConfirm}>{busy?tr("处理中…","Working…"):t.buttons.confirm}</Button></div></Dialog>;}
