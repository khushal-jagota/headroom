import type { FastifyPluginAsync, FastifyRequest } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { dictionaryBody, integerQuery, QueryIntegerError } from '../list_reads/http.ts';
import { DEFAULT_LIST_LIMIT } from '../list_reads/configuration.ts';
import { parseReadDetail, rejectParameters, requireFullForOne } from '../list_reads/logic/index.ts';
import { resolveProject } from '../projects/data/index.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { custom_workflow_from_dict } from '../worker_types/store.ts';
import * as data from './data/index.ts';
import { TITLE_MAX_CHARS } from './contracts.ts';
import type { TicketEdit, ReviewTransferNotice } from './contracts.ts';
import type { WorkflowDefinition } from '../worker_types/contracts.ts';
import { ticket_json, ticket_creation_json, ticket_detail, list_tickets, list_ticket_summaries, copy_text, review_view } from './views.ts';
import type { Json } from './views.ts';
import { notify_review_transfers } from './review_transfer.ts';
import { workspace_ticket, workspace_field } from './workspace/views.ts';
import { add_work_attention, read_running_conversation_count } from '../work_attention.ts';

export function body_str(body:Json,key:string,fallback=''):string {const raw=key in body?body[key]:fallback;if(typeof raw!=='string')throw new PlannerError('validation',`invalid ${key}`,{[key]:raw});return raw;}
export function body_opt_str(body:Json,key:string):string|null {const raw=body[key]??null;if(raw===null)return null;if(typeof raw!=='string')throw new PlannerError('validation',`invalid ${key}`,{[key]:raw});return raw;}
function body_str_list(body:Json,key:string):string[]{const raw=key in body?body[key]:[];if(!Array.isArray(raw)||raw.some(v=>typeof v!=='string'))throw new PlannerError('validation',`invalid ${key}`,{[key]:raw});return raw;}
function record(value:unknown):value is Json{return value!==null&&typeof value==='object'&&!Array.isArray(value);}
export function parse_principal(raw:unknown,field:string,allow_ticket=false):Principal|null {
  if(raw==null)return null;
  if(!record(raw)||Object.keys(raw).sort().join(',')!=='id,kind')throw new PlannerError('validation',`${field} must be a principal object with kind and id`,{[field]:raw});
  const choices=allow_ticket?'owner, chief, manager, or ticket':'owner, chief, or manager';
  // Python PrincipalKind includes goal. Keep its constructor semantics here too.
  if(typeof raw.kind!=='string'||!['owner','chief','manager','goal',...(allow_ticket?['ticket']:[])].includes(raw.kind))throw new PlannerError('validation',`${field} kind must be ${choices}`,{[field]:raw});
  if(typeof raw.id!=='string')throw new PlannerError('validation',`${field} id must be text`,{[field]:raw});
  if(!raw.id||raw.id!==raw.id.trim())throw new PlannerError('validation','principal id must be non-empty and must not contain outer whitespace',{[field]:raw});
  const singleton=raw.kind==='owner'?'owner':raw.kind==='chief'?'chief':null;
  if(singleton!==null&&raw.id!==singleton)throw new PlannerError('validation',`${raw.kind} principal id must be ${singleton}`,{[field]:raw});
  return {kind:raw.kind,id:raw.id} as Principal;
}
function parse_manager(raw:unknown):Principal {const value=parse_principal(raw,'manager');if(value===null||!['chief','manager'].includes(value.kind))throw new PlannerError('validation','manager must name Chief or a Manager');return value;}
function required_principal(raw:unknown,field:string):Principal {const value=parse_principal(raw,field);if(value===null)throw new PlannerError('scope_missing',`approval requires ${field}`,{missing:[field]});return value;}
function stated_holder(raw:unknown,field:string):Principal {const value=parse_principal(raw,field);if(value===null)throw new PlannerError('validation',`${field} must be a principal object with kind and id`,{[field]:raw});return value;}
function validate_field(workflow:WorkflowDefinition,field:string):void {if(!workflow.fields.some(f=>f.id===field))throw new PlannerError('validation','unknown ticket field',{field,workflow:workflow.label});}
function parse_next_ceiling(raw:string|null,workflow:WorkflowDefinition):string|null {if(raw===null||raw==='none')return raw;if(!workflow.stages.some(s=>s.id===raw))throw new PlannerError('scope_invalid','unknown next_ceiling',{next_ceiling:raw});return raw;}
const CREATE_KEYS=['worker_type','workflow','employee_backend','employee_launch_model','employee_launch_reasoning_effort','locale','title','brief','deadline','project','project_id','manager','blocked_by_ticket_ids','ceiling','ceiling_holder'];
export function write_create_ticket(sql:TransactionSql,raw:Json,principal:Principal,now:number):Json {
  if(Object.keys(raw).some(k=>['sprint_id','manager_id','sprint_item_id','day','day_id'].includes(k)))throw new PlannerError('validation','use manager assignment instead of calendar placement');
  for(const field of Object.keys(raw))if(!CREATE_KEYS.includes(field))throw new PlannerError('validation','unknown ticket field',{field});
  const worker_type=body_opt_str(raw,'worker_type'),known=configured_worker_type_registry().supported_worker_types();
  if(!(worker_type===null&&'workflow' in raw)&&!known.includes(worker_type!))throw new PlannerError('validation','ticket create requires a known worker type',{worker_type,worker_types:known});
  const title=body_str(raw,'title'),brief=body_str(raw,'brief'),deadline=body_opt_str(raw,'deadline'),project_name=body_opt_str(raw,'project'),project_id=body_opt_str(raw,'project_id'),blocked_by_ticket_ids=body_str_list(raw,'blocked_by_ticket_ids'),stated_ceiling=body_opt_str(raw,'ceiling');
  const workflow='workflow' in raw?custom_workflow_from_dict(raw.workflow):undefined;
  const locale='locale' in raw?raw.locale:undefined;
  if(locale!==undefined&&locale!=='vps'&&locale!=='macos')throw new PlannerError('validation','locale must be vps or macos',{locale:String(locale)});
  const employee_backend='employee_backend' in raw?body_str(raw,'employee_backend'):undefined,employee_launch_model='employee_launch_model' in raw?body_str(raw,'employee_launch_model'):undefined,employee_launch_reasoning_effort='employee_launch_reasoning_effort' in raw?body_opt_str(raw,'employee_launch_reasoning_effort'):undefined;
  const manager='manager' in raw?parse_manager(raw.manager):{kind:'chief',id:'chief'} as const,project=resolveProject(sql,project_id,project_name);
  const ticket=data.create_ticket(sql,{title,principal,now,title_max_chars:TITLE_MAX_CHARS,brief,project_id:project?.id??(manager.kind==='manager'?null:'project_other'),deadline,manager_id:manager.kind==='manager'?manager.id:null,manager_kind:manager.kind,worker_type,workflow,locale,employee_backend,employee_launch_model,employee_launch_reasoning_effort,reasoning_effort_is_set:'employee_launch_reasoning_effort' in raw,blocked_by_ticket_ids,stated_ceiling,stated_holder:parse_principal(raw.ceiling_holder,'ceiling_holder')});
  return ticket_creation_json(sql,ticket,now);
}
export function write_patch_ticket(sql:TransactionSql,ticket_id:string,body:Json,principal:Principal,now:number,review_transfer_notices:ReviewTransferNotice[]=[]):Json {
  const recognized=['title','deadline','project','project_id','manager','recap','field_values','ceiling','ceiling_holder'];
  for(const field of Object.keys(body))if(!recognized.includes(field))throw new PlannerError('validation','unknown ticket field',{field});
  if(!Object.keys(body).length)throw new PlannerError('validation','no ticket fields to update');
  const edit:TicketEdit={};
  if('title' in body)edit.title=body_str(body,'title');
  if('deadline' in body)edit.deadline=body_opt_str(body,'deadline');
  if('project' in body||'project_id' in body)edit.project_id=resolveProject(sql,body_opt_str(body,'project_id'),body_opt_str(body,'project'))?.id??null;
  if('manager' in body)edit.manager=parse_manager(body.manager);
  if('recap' in body)edit.recap=body_str(body,'recap');
  if('ceiling' in body)edit.ceiling=body_str(body,'ceiling');
  if('ceiling_holder' in body)edit.ceiling_holder=stated_holder(body.ceiling_holder,'ceiling_holder');
  if('field_values' in body) {
    const raw=body.field_values;if(!record(raw)||!Object.keys(raw).length)throw new PlannerError('validation','field_values requires a non-empty object');
    const definition=data.read_ticket(sql,ticket_id).workflow, values:Record<string,string>={};
    for(const [field,value] of Object.entries(raw)){if(typeof value!=='string')throw new PlannerError('validation','a field value must be a string',{field});validate_field(definition,field);values[field]=value;}
    edit.field_values=values;
  }
  return ticket_json(data.edit_ticket(sql,ticket_id,{edit,title_max_chars:TITLE_MAX_CHARS,principal,now,review_transfer_notices}),now);
}
function query_values(request:FastifyRequest):URLSearchParams {return new URL(request.url,'http://panels.invalid').searchParams;}
function boolean_query(value:string|null,key:string):boolean|null {
  if(value===null)return null;
  if(['1','true','t','yes','y','on'].includes(value.toLowerCase()))return true;
  if(['0','false','f','no','n','off'].includes(value.toLowerCase()))return false;
  throw new QueryBooleanError(key,value);
}
class QueryBooleanError extends Error {readonly key:string;readonly value:string;constructor(key:string,value:string){super('boolean query');this.key=key;this.value=value;}payload(){return {type:'bool_parsing',loc:['query',this.key],msg:'Input should be a valid boolean, unable to interpret input',input:this.value};}}
export const routes:FastifyPluginAsync<AreaServices>=async(app,services)=> {
  app.post('/tickets',async(request,reply)=>{if(!dictionaryBody(request.body,reply))return;const raw=request.body;return services.database.write(tx=>write_create_ticket(tx,raw,services.identity(request).principal,services.clock.nowUnix()));});
  app.get('/tickets',async(request,reply)=> {
    const q=query_values(request),last=(k:string)=>q.getAll(k).at(-1)??null, scalar=(k:string)=>last(k)??undefined, array=(k:string)=>q.has(k)?q.getAll(k):null;
    let limit:number|undefined,offset:number|undefined,is_backlogged:boolean|null,include_terminal:boolean;
    const failures:unknown[]=[];
    try{limit=integerQuery(scalar('limit'),'limit');}catch(e){if(e instanceof QueryIntegerError)failures.push(e.payload());else throw e;}
    try{offset=integerQuery(scalar('offset'),'offset');}catch(e){if(e instanceof QueryIntegerError)failures.push(e.payload());else throw e;}
    try{is_backlogged=boolean_query(last('is_backlogged'),'is_backlogged');}catch(e){if(e instanceof QueryBooleanError)failures.push(e.payload());else throw e;}
    try{include_terminal=boolean_query(last('include_terminal'),'include_terminal')??false;}catch(e){if(e instanceof QueryBooleanError)failures.push(e.payload());else throw e;}
    if(failures.length){reply.code(422).send({detail:failures});return;}
    const level=parseReadDetail(scalar('detail')),id=last('id'),stage=array('stage'),exclude_stage=array('exclude_stage'),ticket_status=array('ticket_status'),exclude_ticket_status=array('exclude_ticket_status'),worker_type=array('worker_type'),search=last('search'),project=last('project'),project_id=last('project_id'),manager_id=last('manager_id'),manager_kind=last('manager_kind');
    const [response,rows]=services.database.read((sql):[Json,Json[]]=> {
      const scope={stage,exclude_stage,ticket_status,exclude_ticket_status,is_backlogged,worker_type,include_terminal:include_terminal||null,search,project,project_id,manager_id,manager_kind,limit,offset};
      if(id!==null){requireFullForOne(level);rejectParameters('id',scope);const one=ticket_detail(sql,id,services.clock.nowUnix(),services.config.db_path);return [one,[one]];}
      const resolved=resolveProject(sql,project_id,project);
      if(level==='full') {
        rejectParameters('detail=full',{manager_kind,manager_id,is_backlogged,worker_type,exclude_stage,ticket_status,exclude_ticket_status,include_terminal:include_terminal||null,search,limit,offset});
        if(stage!==null&&stage.length>1)throw new PlannerError('validation','detail=full takes at most one stage',{stages:stage});
        const rows=list_tickets(sql,services.clock.nowUnix(),stage?.[0]??null,resolved?.id??null,manager_id);return [{tickets:rows},rows];
      }
      const manager=manager_kind!==null||manager_id!==null?parse_manager({kind:manager_kind??'manager',id:manager_kind==='chief'&&manager_id===null?'chief':manager_id}):null;
      const registry=configured_worker_type_registry(),terminal=new Set(registry.registered_worker_types().map(w=>registry.require(w).stages.at(-1)!.id));
      const requested_terminal=[...new Set(stage??[])].filter(s=>terminal.has(s)).sort();
      if(requested_terminal.length&&!include_terminal)throw new PlannerError('validation','terminal stages require include_terminal',{stages:requested_terminal});
      for(const [name,values] of [['ticket_status',ticket_status],['exclude_ticket_status',exclude_ticket_status]] as const)for(const value of values??[])if(!['empty','blocked','agent','awaiting_approval','errored'].includes(value))throw new PlannerError('validation',`invalid ${name}`,{[name]:value});
      const result=list_ticket_summaries(sql,{limit:limit??DEFAULT_LIST_LIMIT,offset:offset??0},{manager,stages:stage??[],excluded_stages:exclude_stage??[],ticket_statuses:ticket_status??[],excluded_ticket_statuses:exclude_ticket_status??[],is_backlogged:is_backlogged!,worker_types:worker_type??[],include_terminal:include_terminal!,search},resolved?.id??null,manager_id);return [result,result.tickets];
    });
    await add_work_attention(services.database,services.conversationSystem,rows);
    return response;
  });
  app.get<{Params:{ticket_id:string}}>('/tickets/:ticket_id/worker-self',async request=>services.database.read(sql=>{
    const t=data.read_ticket(sql,request.params.ticket_id);
    if(t.conversation_id!==null){const owner=data.read_ticket_by_conversation_id(sql,t.conversation_id);if(owner.id!==t.id)throw new PlannerError('validation','ticket durable session is owned by another ticket',{ticket_id:t.id,conversation_id:t.conversation_id,owner_ticket_id:owner.id});}
    return ticket_detail(sql,t.id,services.clock.nowUnix(),services.config.db_path);
  }));
  app.patch<{ Params: { ticket_id: string } }>('/tickets/:ticket_id', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const raw = request.body;
    const notices: ReviewTransferNotice[] = [];
    const now = services.clock.nowUnix();
    const result = services.database.write(tx => write_patch_ticket(tx, request.params.ticket_id,
      raw, services.identity(request).principal, now, notices));
    if (services.conversationSystem) {
      await notify_review_transfers(services.conversationSystem, services.database, notices, { now });
    }
    return result;
  });
  app.post<{Params:{ticket_id:string}}>('/tickets/:ticket_id/propose',async(request,reply)=>{
    if(!dictionaryBody(request.body,reply))return;
    const principal=services.identity(request).principal,id=request.params.ticket_id;
    if(principal.kind!=='ticket'||principal.id!==id)throw new PlannerError('agent_forbidden',"only the Ticket's own Worker can file its proposal",{ticket_id:id});
    const body=body_str(request.body,'body'),now=services.clock.nowUnix();return services.database.write(tx=>ticket_json(data.file_current_proposal(tx,id,{body,principal,now}),now));
  });
  app.post<{Params:{ticket_id:string;field:string}}>('/tickets/:ticket_id/accept/:field',async(request,reply)=>{
    if(!dictionaryBody(request.body,reply))return;
    const raw=request.body,revision=raw.expected_proposal_revision??null;
    if(revision!==null&&(typeof revision!=='number'||!Number.isInteger(revision)||revision<=0))throw new PlannerError('validation','expected_proposal_revision must be a positive integer');
    const edited_body=body_opt_str(raw,'edited_body'),next=body_opt_str(raw,'next_ceiling'),id=request.params.ticket_id,field=request.params.field;
    const definition=services.database.read(sql=>data.read_ticket(sql,id).workflow);validate_field(definition,field);
    const next_ceiling=parse_next_ceiling(next,definition),next_holder=required_principal(raw.next_holder,'next_holder'),now=services.clock.nowUnix();
    return services.database.write(tx=>ticket_json(data.accept_proposal(tx,id,field,{principal:services.identity(request).principal,now,edited_body,next_ceiling,next_holder,expected_proposal_revision:revision as number|null}),now));
  });
  app.post<{Params:{ticket_id:string}}>('/tickets/:ticket_id/reject',async(request,reply)=>{if(!dictionaryBody(request.body,reply))return;const message=body_opt_str(request.body,'message'),now=services.clock.nowUnix();return services.database.write(tx=>ticket_json(data.reject_proposal(tx,request.params.ticket_id,{message,principal:services.identity(request).principal,now}),now));});
  app.post<{Params:{ticket_id:string;field:string}}>('/tickets/:ticket_id/complete/:field',async(request,reply)=>{
    if(!dictionaryBody(request.body,reply))return;const body=body_str(request.body,'body'),id=request.params.ticket_id,field=request.params.field,ticket=services.database.read(sql=>data.read_ticket(sql,id));validate_field(ticket.workflow,field);
    if(ticket.conversation_id&&services.conversationSystem&&await services.conversationSystem.is_running(ticket.conversation_id))throw new PlannerError('already_running','ticket conversation is still running',{ticket_id:id});
    const now=services.clock.nowUnix();return services.database.write(tx=>ticket_json(data.complete_user_owned_gate(tx,id,field,{new_body:body,principal:services.identity(request).principal,now}),now));
  });
  for(const [action,is_backlogged] of [['backlog',true],['resume',false]] as const)app.post<{Params:{ticket_id:string}}>(`/tickets/:ticket_id/${action}`,async(request,reply)=>{
    if(!dictionaryBody(request.body,reply))return;const id=request.params.ticket_id,now=services.clock.nowUnix();services.database.write(tx=>data.set_ticket_backlog(tx,id,{is_backlogged,principal:services.identity(request).principal,now}));return services.database.read(sql=>ticket_detail(sql,id,now,services.config.db_path));
  });
  app.get<{Params:{ticket_id:string}}>('/tickets/:ticket_id/copy-text',async(request,reply)=>{reply.type('text/plain; charset=utf-8');return services.database.read(sql=>copy_text(sql,request.params.ticket_id));});
  app.get('/review',async()=>{const response=services.database.read(review_view);response.running_worker_count=await read_running_conversation_count(services.database,services.conversationSystem);return response;});
  app.get<{Params:{ticket_id:string}}>('/tickets/:ticket_id/workspace',async request=>services.database.read(sql=>workspace_ticket(sql,request.params.ticket_id)));
  app.get<{Params:{ticket_id:string;field:string}}>('/tickets/:ticket_id/workspace/fields/:field',async request=>services.database.read(sql=>workspace_field(sql,request.params.ticket_id,request.params.field)));
};
