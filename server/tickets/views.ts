/** Public Ticket reads. All queries share the caller's read snapshot. */
import { dirname, join, parse } from 'node:path';
import { existsSync, lstatSync, readdirSync, statSync } from 'node:fs';
import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { Ticket } from './contracts.ts';
import * as data from './data/index.ts';
import { blocker_summary } from '../core/ticket_blocks.ts';
import { workflow_manifest } from '../worker_types/registry.ts';
import { checkPage, pageFacts } from '../list_reads/logic/index.ts';
import type { ListPageRequest } from '../list_reads/contracts.ts';
export type Json = Record<string, unknown>;

export function broken_ticket(sql: ReadSql, id: string): Json | null {
  const row = sql.get<Record<string, unknown>>('SELECT t.*, CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END AS effective_project_id, p.name AS project_name, m.title AS manager_title FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id LEFT JOIN projects p ON p.id=CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END WHERE t.id=?', [id]);
  if (!row?.integrity_error_reason) return null;
  const structural = new Set(['PlannerError: unknown worker type (worker_type)', 'PlannerError: stage outside the linear order (stage)', "PlannerError: ceiling outside the type's range (ceiling)", 'PlannerError: ceiling holder does not exist (ceiling_holder)', 'PlannerError: a Ticket cannot hold its own ceiling (ceiling_holder)', 'PlannerError: corrupt ticket field state (fields or proposal)', 'PlannerError: unknown agent backend (employee_backend)', 'PlannerError: invalid stored Ticket structure', 'JSONDecodeError: malformed stored JSON', 'KeyError: missing stored Ticket member', 'TypeError: invalid stored Ticket member type', 'ValueError: invalid stored Ticket member value']);
  const reason = structural.has(String(row.integrity_error_reason)) ? row.integrity_error_reason : 'TicketIntegrityError: structural fault reason unavailable';
  return {id: row.id, title: row.title, worker_type: row.worker_type, stage: row.stage, ticket_status: 'errored', integrity_error: {reason}, recap: row.recap, conversation_id: row.conversation_id, manager_id: row.manager_id, manager_kind: row.manager_kind, manager_title: row.manager_title, project_id: row.effective_project_id, project: row.project_name, is_backlogged: Boolean(row.is_backlogged), created_at: row.created_at, updated_at: row.updated_at};
}
export function blocker_summary_json(summary: ReturnType<typeof blocker_summary>): Json {
  return {blocked_by: summary.blocked_by.filter(r => r.active).map(r => ({ticket_id:r.ticket_id,title:r.title,stage:r.stage,href:r.href}))};
}
export function ticket_json(ticket: Ticket, _now = 0): Json {
  return {is_backlogged:ticket.is_backlogged,id:ticket.id,title:ticket.title,worker_type:ticket.worker_type,workflow:workflow_manifest(ticket.workflow,ticket.worker_type,ticket.workflow.specialist_skill),locale:ticket.locale,employee_backend:ticket.employee_backend,employee_launch_model:ticket.employee_launch_model,employee_launch_reasoning_effort:ticket.employee_launch_reasoning_effort,stage:ticket.stage,deadline:ticket.deadline,project_id:ticket.project_id,project:ticket.project_name,manager_id:ticket.manager_id,manager:{kind:ticket.manager_kind,id:ticket.manager_kind==='manager'?ticket.manager_id:'chief'},manager_title:ticket.manager_title,recap:ticket.recap,ceiling:ticket.ceiling,ceiling_holder:ticket.ceiling_holder,ticket_status:ticket.ticket_status,conversation_id:ticket.conversation_id,field_values:{...ticket.field_values},field_approvals:ticket.field_approvals,pending_proposal:ticket.pending_proposal===null?null:{...ticket.pending_proposal,revision:ticket.pending_proposal_revision},created_at:ticket.created_at,updated_at:ticket.updated_at};
}
export function ticket_creation_json(sql: ReadSql, ticket: Ticket, now: number): Json {
  const holder=ticket.ceiling_holder;
  const recipient=holder.kind==='chief'?'Chief':holder.kind==='manager'?`the manager ${sql.get<{title:string}>('SELECT title FROM managers WHERE id=?',[holder.id])!.title}`:'the owner';
  const stages=ticket.workflow.stages.filter(s=>!s.is_terminal), current=ticket.workflow.stages.findIndex(s=>s.id===ticket.stage), cap=ticket.workflow.stages.findIndex(s=>s.id===ticket.ceiling);
  const remaining=stages.filter(s=>ticket.workflow.stages.indexOf(s)>=current);
  const automatic=remaining.filter(s=>ticket.workflow.stages.indexOf(s)<cap), autonomous=automatic.filter(s=>s.ownership_mode==='worker'), user=automatic.filter(s=>s.ownership_mode==='user');
  const review=remaining.find(s=>!automatic.includes(s));
  const parts:string[]=[];
  if (!review) parts.push('Nothing in this grant ever parks for review.');
  parts.push(autonomous.length?'Runs unsupervised: '+autonomous.map(s=>s.label).join(', ')+'.':'Nothing runs unsupervised.');
  if(user.length) parts.push('User involvement is required at '+user.map(s=>s.label).join(', ')+', even though its proposals auto-accept.');
  if(review) {parts.push(`First parks for review at ${review.label}, with ${recipient}.`); if(review.ownership_mode==='user') parts.push(`${review.label} requires user involvement before its proposal review.`);}
  return {...ticket_json(ticket,now),supervision_receipt:parts.join(' ')};
}
interface Artifact { name:string; opens:string|null; modified_at:number; children:Artifact[] }
function newest(path:string):number {return Math.max(0,...readdirSync(path).filter(n=>!lstatSync(join(path,n)).isSymbolicLink()).map(n=>{const p=join(path,n),s=statSync(p);return s.isFile()?s.mtimeMs/1000:s.isDirectory()?newest(p):0;}));}
function fold(root:string,prefix=''):Artifact[] {
  if(!existsSync(root)||lstatSync(root).isSymbolicLink()||!statSync(root).isDirectory()) return [];
  const result:Artifact[]=[];
  for(const name of readdirSync(root).sort()) {
    const path=join(root,name),s=lstatSync(path), opens=prefix+name;
    if(s.isSymbolicLink()) continue;
    if(s.isFile()) result.push({name,opens,modified_at:s.mtimeMs/1000,children:[]});
    else if(s.isDirectory()) {
      const index=join(path,'index.html');
      if(existsSync(index)&&!lstatSync(index).isSymbolicLink()&&statSync(index).isFile()) result.push({name,opens:opens+'/index.html',modified_at:newest(path),children:[]});
      else {const children=fold(path,opens+'/');if(children.length) result.push({name,opens:null,modified_at:Math.max(...children.map(c=>c.modified_at)),children});}
    }
  }
  return result.sort((a,b)=>b.modified_at-a.modified_at||(a.name.toLowerCase()<b.name.toLowerCase()?-1:a.name.toLowerCase()>b.name.toLowerCase()?1:a.name<b.name?-1:a.name>b.name?1:0));
}
function ticket_artifacts(filename:string|undefined,id:string):Artifact[] {
  if(!filename) return [];
  const root=join(dirname(filename),'files','tickets',id);
  for(let p=root;;p=dirname(p)) {if(existsSync(p)&&lstatSync(p).isSymbolicLink()) return [];if(p===parse(p).root) break;}
  return fold(root);
}
export function ticket_detail(sql: ReadSql,id:string,now:number,db_path?:string):Json {
  const broken=broken_ticket(sql,id);if(broken) return broken;
  const ticket=data.read_ticket(sql,id), summary=blocker_summary(sql,id);
  const result={...ticket_json(ticket,now),record_sha256:data.ticket_record_sha256(sql,id),artifacts:ticket_artifacts(db_path,id),blocked:summary.blocked,employee_configuration_editable:data.employee_configuration_editable(ticket),conversation_history:sql.all<{conversation_id:string;created_at:number}>('SELECT tc.conversation_id,c.created_at FROM ticket_conversations tc JOIN conversations c ON c.conversation_id=tc.conversation_id WHERE tc.ticket_id=? ORDER BY c.created_at,tc.conversation_id',[id])};
  return summary.blocked?{...result,blocker_summary:blocker_summary_json(summary)}:result;
}
export function list_tickets(sql:ReadSql,now:number,stage:string|null,project_id:string|null,manager_id:string|null):Json[] {
  const clauses:string[]=[],params:string[]=[];
  if(stage!==null){clauses.push('t.stage=?');params.push(stage);}
  if(project_id!==null){clauses.push('CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END=?');params.push(project_id);}
  if(manager_id!==null){clauses.push('t.manager_id=?');params.push(manager_id);}
  return sql.all<{id:string}>('SELECT t.id FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id'+(clauses.length?' WHERE '+clauses.join(' AND '):'')+' ORDER BY t.created_at,t.id',params).map(r=>broken_ticket(sql,r.id)??ticket_json(data.read_ticket(sql,r.id),now));
}
export interface TicketListFilters {manager:Principal|null;stages:string[];excluded_stages:string[];ticket_statuses:string[];excluded_ticket_statuses:string[];is_backlogged:boolean|null;worker_types:string[];include_terminal:boolean;search:string|null}
export function list_ticket_summaries(sql:ReadSql,page:ListPageRequest,filters:TicketListFilters,project_id:string|null,manager_id:string|null):{tickets:Json[];page:ReturnType<typeof pageFacts>} {
  checkPage(page);
  const records=sql.all<{id:string;value:string;project_name:string|null;manager_title:string|null;recap:string;field_values:string;pending_proposal:string|null}>("SELECT r.id,r.value,p.name AS project_name,m.title AS manager_title,t.recap,t.field_values,t.pending_proposal FROM navigation_records r LEFT JOIN managers m ON m.id=json_extract(r.value,'$.manager_id') LEFT JOIN projects p ON p.id=json_extract(r.value,'$.project_id') JOIN tickets t ON t.id=r.id WHERE r.kind='ticket' ORDER BY json_extract(r.value,'$.created_at'),r.id");
  const matches:Json[]=[];
  for(const row of records) {
    const v=JSON.parse(row.value) as Json, status=String(v.ticket_status);
    if(filters.manager&&(v.manager_kind!==filters.manager.kind||(filters.manager.kind==='manager'&&v.manager_id!==filters.manager.id))) continue;
    if(project_id!==null&&v.project_id!==project_id||manager_id!==null&&v.manager_id!==manager_id) continue;
    if(filters.ticket_statuses.length&&!filters.ticket_statuses.includes(status)||filters.excluded_ticket_statuses.includes(status)) continue;
    if('integrity_error' in v) {const broken=broken_ticket(sql,row.id)!;if(filters.search!==null&&!casefold(python_repr(broken)).includes(casefold(filters.search)))continue;matches.push(broken);continue;}
    if(filters.is_backlogged!==null&&Boolean(v.is_backlogged)!==filters.is_backlogged)continue;
    if(filters.worker_types.length&&!filters.worker_types.includes(String(v.worker_type)))continue;
    if(!filters.include_terminal&&v.terminal)continue;
    if(filters.stages.length&&!filters.stages.includes(String(v.stage))||filters.excluded_stages.includes(String(v.stage)))continue;
    if(filters.search) {const text=[v.title,row.recap,...Object.values(data.read_ticket(sql,row.id).field_values),row.pending_proposal?(JSON.parse(row.pending_proposal) as {body:string}).body:''].join('\n');if(!casefold(text).includes(casefold(filters.search)))continue;}
    matches.push({is_backlogged:Boolean(v.is_backlogged),waiting_to_closeout:v.gating_field==='consequences'&&status==='empty',id:row.id,title:v.title,locale:v.locale,worker_type:v.worker_type,stage:v.stage,ticket_status:status,project_id:v.project_id,project:row.project_name,manager_id:v.manager_id,manager:{kind:v.manager_kind,id:v.manager_id??'chief'},manager_title:row.manager_title,recap_preview:v.recap_preview});
  }
  const tickets=matches.slice(page.offset,page.offset+page.limit);return {tickets,page:pageFacts(matches.length,tickets.length,page)};
}
export function copy_text(sql:ReadSql,id:string):string {
  const broken=broken_ticket(sql,id);if(broken)return python_repr(broken);
  const ticket=data.read_ticket(sql,id),ownership=ticket.workflow.stages.find(s=>s.id===ticket.stage)?.ownership_mode??null,summary=blocker_summary(sql,id);
  const show=(value:string|null|undefined)=>value||'(none)';
  const field_blocks=ticket.workflow.fields.map(f=>`${f.id}:\n${show(ticket.field_values[f.id])}\n\n`).join('');
  const blockers=summary.blocked_by.filter(r=>r.active).map(r=>`- ${r.title} (${r.ticket_id}, ${r.stage})`).join('\n')||'(none)';
  return `${ticket.title}\nstage: ${ticket.stage}\nemployee_backend: ${ticket.employee_backend}\nowner: ${ownership??'(none)'}\n\n${field_blocks}pending proposal:\n${show(ticket.pending_proposal?.body)}\nrecap:\n${show(ticket.recap)}\n\nblocked_by:\n${blockers}\n`;
}
function python_repr(value:unknown):string {if(value===null)return 'None';if(value===true)return 'True';if(value===false)return 'False';if(typeof value==='string')return "'"+value.replaceAll('\\','\\\\').replaceAll("'","\\'").replaceAll('\n','\\n')+"'";if(Array.isArray(value))return '['+value.map(python_repr).join(', ')+']';if(typeof value==='object')return '{'+Object.entries(value as Json).map(([k,v])=>python_repr(k)+': '+python_repr(v)).join(', ')+'}';return String(value);}
export function review_view(sql:ReadSql):Json {
  const items:Json[]=[];
  for(const {id} of sql.all<{id:string}>('SELECT id FROM tickets ORDER BY id')) {
    if(broken_ticket(sql,id))continue;
    const t=data.read_ticket(sql,id),field=t.workflow.stages.find(s=>s.id===t.stage)?.gating_field;
    if(t.ticket_status==='awaiting_approval'&&!t.is_backlogged&&t.ceiling_holder.kind==='owner'&&field&&t.pending_proposal)items.push({review_item_type:'proposal',ticket_id:id,field,title:t.title,waiting_since:t.pending_proposal.created_at});
  }
  const messages:Json[]=[];
  for(const conversation of sql.all<{id:string;value:string}>("SELECT id,value FROM navigation_records WHERE kind='conversation' ORDER BY id")) {
    const c=JSON.parse(conversation.value) as {attention:{durable:{message_for_owner:boolean}};first_unread_owner_message_at:number};
    if(!c.attention.durable.message_for_owner)continue;
    const row=sql.get<{id:string;value:string}>("SELECT a.id,a.value FROM navigation_records a JOIN navigation_records owner ON owner.kind=json_extract(a.value,'$.owner_kind') AND owner.id=json_extract(a.value,'$.owner_id') WHERE a.kind='address' AND json_extract(a.value,'$.conversation_id')=? ORDER BY json_extract(a.value,'$.current') DESC,a.id LIMIT 1",[conversation.id]);
    if(!row)continue;
    const address=JSON.parse(row.value) as {owner_kind:string;owner_id:string;current:boolean};
    const owner=JSON.parse(sql.get<{value:string}>('SELECT value FROM navigation_records WHERE kind=? AND id=?',[address.owner_kind,address.owner_id])!.value) as Json;
    if(address.owner_kind==='ticket'&&(owner.stage==='done'||owner.is_backlogged)||address.owner_kind==='manager'&&owner.settled)continue;
    messages.push({review_item_type:'message',conversation_id:conversation.id,address_id:row.id,owner_kind:address.owner_kind,owner_id:address.owner_id,title:owner.title??owner.label??'',waiting_since:c.first_unread_owner_message_at,recap:owner.recap??'',recap_reference:null,_current:address.current});
  }
  messages.sort((a,b)=>Number(b._current)-Number(a._current)||String(a.address_id).localeCompare(String(b.address_id))||String(a.conversation_id).localeCompare(String(b.conversation_id)));
  for(const m of messages){delete m._current;items.push(m);}
  const tickets=new Map<string,Json>(),result:Json[]=[];
  for(const item of items) {
    if(item.review_item_type==='message'&&item.owner_kind!=='ticket'){result.push(item);continue;}
    const id=String(item.review_item_type==='proposal'?item.ticket_id:item.owner_id);
    let card=tickets.get(id);if(!card){card={review_item_type:'ticket',ticket_id:id,title:item.title,waiting_since:item.waiting_since,proposal:null,messages:[]};tickets.set(id,card);}
    card.waiting_since=Math.min(Number(card.waiting_since),Number(item.waiting_since));
    if(item.review_item_type==='proposal')card.proposal=item;else (card.messages as Json[]).push(item);
  }
  result.push(...tickets.values());
  result.sort((a,b)=>Number(a.waiting_since)-Number(b.waiting_since)||String(a.review_item_type).localeCompare(String(b.review_item_type))||String(a.conversation_id??a.ticket_id).localeCompare(String(b.conversation_id??b.ticket_id)));
  return {items:result};
}

// Unicode full folding preserves Python search semantics beyond lowercase.
const CASEFOLD:Readonly<Record<string,string>>={"\u00b5":"\u03bc","\u00df":"ss","\u0149":"\u02bcn","\u017f":"s","\u01f0":"j\u030c","\u0345":"\u03b9","\u0390":"\u03b9\u0308\u0301","\u03b0":"\u03c5\u0308\u0301","\u03c2":"\u03c3","\u03d0":"\u03b2","\u03d1":"\u03b8","\u03d5":"\u03c6","\u03d6":"\u03c0","\u03f0":"\u03ba","\u03f1":"\u03c1","\u03f5":"\u03b5","\u0587":"\u0565\u0582","\u13a0":"\u13a0","\u13a1":"\u13a1","\u13a2":"\u13a2","\u13a3":"\u13a3","\u13a4":"\u13a4","\u13a5":"\u13a5","\u13a6":"\u13a6","\u13a7":"\u13a7","\u13a8":"\u13a8","\u13a9":"\u13a9","\u13aa":"\u13aa","\u13ab":"\u13ab","\u13ac":"\u13ac","\u13ad":"\u13ad","\u13ae":"\u13ae","\u13af":"\u13af","\u13b0":"\u13b0","\u13b1":"\u13b1","\u13b2":"\u13b2","\u13b3":"\u13b3","\u13b4":"\u13b4","\u13b5":"\u13b5","\u13b6":"\u13b6","\u13b7":"\u13b7","\u13b8":"\u13b8","\u13b9":"\u13b9","\u13ba":"\u13ba","\u13bb":"\u13bb","\u13bc":"\u13bc","\u13bd":"\u13bd","\u13be":"\u13be","\u13bf":"\u13bf","\u13c0":"\u13c0","\u13c1":"\u13c1","\u13c2":"\u13c2","\u13c3":"\u13c3","\u13c4":"\u13c4","\u13c5":"\u13c5","\u13c6":"\u13c6","\u13c7":"\u13c7","\u13c8":"\u13c8","\u13c9":"\u13c9","\u13ca":"\u13ca","\u13cb":"\u13cb","\u13cc":"\u13cc","\u13cd":"\u13cd","\u13ce":"\u13ce","\u13cf":"\u13cf","\u13d0":"\u13d0","\u13d1":"\u13d1","\u13d2":"\u13d2","\u13d3":"\u13d3","\u13d4":"\u13d4","\u13d5":"\u13d5","\u13d6":"\u13d6","\u13d7":"\u13d7","\u13d8":"\u13d8","\u13d9":"\u13d9","\u13da":"\u13da","\u13db":"\u13db","\u13dc":"\u13dc","\u13dd":"\u13dd","\u13de":"\u13de","\u13df":"\u13df","\u13e0":"\u13e0","\u13e1":"\u13e1","\u13e2":"\u13e2","\u13e3":"\u13e3","\u13e4":"\u13e4","\u13e5":"\u13e5","\u13e6":"\u13e6","\u13e7":"\u13e7","\u13e8":"\u13e8","\u13e9":"\u13e9","\u13ea":"\u13ea","\u13eb":"\u13eb","\u13ec":"\u13ec","\u13ed":"\u13ed","\u13ee":"\u13ee","\u13ef":"\u13ef","\u13f0":"\u13f0","\u13f1":"\u13f1","\u13f2":"\u13f2","\u13f3":"\u13f3","\u13f4":"\u13f4","\u13f5":"\u13f5","\u13f8":"\u13f0","\u13f9":"\u13f1","\u13fa":"\u13f2","\u13fb":"\u13f3","\u13fc":"\u13f4","\u13fd":"\u13f5","\u1c80":"\u0432","\u1c81":"\u0434","\u1c82":"\u043e","\u1c83":"\u0441","\u1c84":"\u0442","\u1c85":"\u0442","\u1c86":"\u044a","\u1c87":"\u0463","\u1c88":"\ua64b","\u1e96":"h\u0331","\u1e97":"t\u0308","\u1e98":"w\u030a","\u1e99":"y\u030a","\u1e9a":"a\u02be","\u1e9b":"\u1e61","\u1e9e":"ss","\u1f50":"\u03c5\u0313","\u1f52":"\u03c5\u0313\u0300","\u1f54":"\u03c5\u0313\u0301","\u1f56":"\u03c5\u0313\u0342","\u1f80":"\u1f00\u03b9","\u1f81":"\u1f01\u03b9","\u1f82":"\u1f02\u03b9","\u1f83":"\u1f03\u03b9","\u1f84":"\u1f04\u03b9","\u1f85":"\u1f05\u03b9","\u1f86":"\u1f06\u03b9","\u1f87":"\u1f07\u03b9","\u1f88":"\u1f00\u03b9","\u1f89":"\u1f01\u03b9","\u1f8a":"\u1f02\u03b9","\u1f8b":"\u1f03\u03b9","\u1f8c":"\u1f04\u03b9","\u1f8d":"\u1f05\u03b9","\u1f8e":"\u1f06\u03b9","\u1f8f":"\u1f07\u03b9","\u1f90":"\u1f20\u03b9","\u1f91":"\u1f21\u03b9","\u1f92":"\u1f22\u03b9","\u1f93":"\u1f23\u03b9","\u1f94":"\u1f24\u03b9","\u1f95":"\u1f25\u03b9","\u1f96":"\u1f26\u03b9","\u1f97":"\u1f27\u03b9","\u1f98":"\u1f20\u03b9","\u1f99":"\u1f21\u03b9","\u1f9a":"\u1f22\u03b9","\u1f9b":"\u1f23\u03b9","\u1f9c":"\u1f24\u03b9","\u1f9d":"\u1f25\u03b9","\u1f9e":"\u1f26\u03b9","\u1f9f":"\u1f27\u03b9","\u1fa0":"\u1f60\u03b9","\u1fa1":"\u1f61\u03b9","\u1fa2":"\u1f62\u03b9","\u1fa3":"\u1f63\u03b9","\u1fa4":"\u1f64\u03b9","\u1fa5":"\u1f65\u03b9","\u1fa6":"\u1f66\u03b9","\u1fa7":"\u1f67\u03b9","\u1fa8":"\u1f60\u03b9","\u1fa9":"\u1f61\u03b9","\u1faa":"\u1f62\u03b9","\u1fab":"\u1f63\u03b9","\u1fac":"\u1f64\u03b9","\u1fad":"\u1f65\u03b9","\u1fae":"\u1f66\u03b9","\u1faf":"\u1f67\u03b9","\u1fb2":"\u1f70\u03b9","\u1fb3":"\u03b1\u03b9","\u1fb4":"\u03ac\u03b9","\u1fb6":"\u03b1\u0342","\u1fb7":"\u03b1\u0342\u03b9","\u1fbc":"\u03b1\u03b9","\u1fbe":"\u03b9","\u1fc2":"\u1f74\u03b9","\u1fc3":"\u03b7\u03b9","\u1fc4":"\u03ae\u03b9","\u1fc6":"\u03b7\u0342","\u1fc7":"\u03b7\u0342\u03b9","\u1fcc":"\u03b7\u03b9","\u1fd2":"\u03b9\u0308\u0300","\u1fd3":"\u03b9\u0308\u0301","\u1fd6":"\u03b9\u0342","\u1fd7":"\u03b9\u0308\u0342","\u1fe2":"\u03c5\u0308\u0300","\u1fe3":"\u03c5\u0308\u0301","\u1fe4":"\u03c1\u0313","\u1fe6":"\u03c5\u0342","\u1fe7":"\u03c5\u0308\u0342","\u1ff2":"\u1f7c\u03b9","\u1ff3":"\u03c9\u03b9","\u1ff4":"\u03ce\u03b9","\u1ff6":"\u03c9\u0342","\u1ff7":"\u03c9\u0342\u03b9","\u1ffc":"\u03c9\u03b9","\uab70":"\u13a0","\uab71":"\u13a1","\uab72":"\u13a2","\uab73":"\u13a3","\uab74":"\u13a4","\uab75":"\u13a5","\uab76":"\u13a6","\uab77":"\u13a7","\uab78":"\u13a8","\uab79":"\u13a9","\uab7a":"\u13aa","\uab7b":"\u13ab","\uab7c":"\u13ac","\uab7d":"\u13ad","\uab7e":"\u13ae","\uab7f":"\u13af","\uab80":"\u13b0","\uab81":"\u13b1","\uab82":"\u13b2","\uab83":"\u13b3","\uab84":"\u13b4","\uab85":"\u13b5","\uab86":"\u13b6","\uab87":"\u13b7","\uab88":"\u13b8","\uab89":"\u13b9","\uab8a":"\u13ba","\uab8b":"\u13bb","\uab8c":"\u13bc","\uab8d":"\u13bd","\uab8e":"\u13be","\uab8f":"\u13bf","\uab90":"\u13c0","\uab91":"\u13c1","\uab92":"\u13c2","\uab93":"\u13c3","\uab94":"\u13c4","\uab95":"\u13c5","\uab96":"\u13c6","\uab97":"\u13c7","\uab98":"\u13c8","\uab99":"\u13c9","\uab9a":"\u13ca","\uab9b":"\u13cb","\uab9c":"\u13cc","\uab9d":"\u13cd","\uab9e":"\u13ce","\uab9f":"\u13cf","\uaba0":"\u13d0","\uaba1":"\u13d1","\uaba2":"\u13d2","\uaba3":"\u13d3","\uaba4":"\u13d4","\uaba5":"\u13d5","\uaba6":"\u13d6","\uaba7":"\u13d7","\uaba8":"\u13d8","\uaba9":"\u13d9","\uabaa":"\u13da","\uabab":"\u13db","\uabac":"\u13dc","\uabad":"\u13dd","\uabae":"\u13de","\uabaf":"\u13df","\uabb0":"\u13e0","\uabb1":"\u13e1","\uabb2":"\u13e2","\uabb3":"\u13e3","\uabb4":"\u13e4","\uabb5":"\u13e5","\uabb6":"\u13e6","\uabb7":"\u13e7","\uabb8":"\u13e8","\uabb9":"\u13e9","\uabba":"\u13ea","\uabbb":"\u13eb","\uabbc":"\u13ec","\uabbd":"\u13ed","\uabbe":"\u13ee","\uabbf":"\u13ef","\ufb00":"ff","\ufb01":"fi","\ufb02":"fl","\ufb03":"ffi","\ufb04":"ffl","\ufb05":"st","\ufb06":"st","\ufb13":"\u0574\u0576","\ufb14":"\u0574\u0565","\ufb15":"\u0574\u056b","\ufb16":"\u057e\u0576","\ufb17":"\u0574\u056d"};
function casefold(value:string):string{return Array.from(value,c=>CASEFOLD[c]??c.toLowerCase()).join('');}
