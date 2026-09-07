import type { ButtonHTMLAttributes, HTMLAttributes, PropsWithChildren } from "react";
import type { ReactNode } from "react";
const cx=(...v:Array<string|false|undefined>)=>v.filter(Boolean).join(" ");
export function Button({className,...props}:ButtonHTMLAttributes<HTMLButtonElement>){return <button className={cx("ui-button",className)} {...props}/>}
export function IconButton({className,...props}:ButtonHTMLAttributes<HTMLButtonElement>){return <button className={cx("ui-icon-button",className)} {...props}/>}
export function Badge({children,className}:{children:ReactNode;className?:string}){return <span className={cx("ui-badge",className)}>{children}</span>}
export function Panel({children,className,...props}:PropsWithChildren<HTMLAttributes<HTMLElement>>){return <section className={cx("ui-panel",className)} {...props}>{children}</section>}
export function EmptyState({title,children}:{title:string;children?:ReactNode}){return <div className="ui-empty"><strong>{title}</strong>{children&&<span>{children}</span>}</div>}
export function StatusBanner({children,className}:{children:ReactNode;className?:string}){return <div role="status" className={cx("ui-status",className)}>{children}</div>}
export function TreeRow({children,className,...props}:PropsWithChildren<HTMLAttributes<HTMLDivElement>>){return <div role="treeitem" className={cx("ui-tree-row",className)} {...props}>{children}</div>}
export function Tab({children,...props}:PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>>){return <button role="tab" className="ui-tab" {...props}>{children}</button>}
