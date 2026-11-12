import type { FastifyRequest } from 'fastify';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../resource_versions/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { field_revision, workspace_field, workspace_ticket } from './workspace/views.ts';

export class TicketWorkspaceResources implements ResourceProvider {
  capture(sql:ReadSql,_request:FastifyRequest,key:string):ResourceSnapshot|null {
    const match=/^\/api\/tickets\/([^/?]+)\/workspace(?:\/fields\/([^/?]+))?$/.exec(key);
    if(!match)return null;
    const ticket_id=decodeURIComponent(match[1]);
    if(match[2]!==undefined){const field=decodeURIComponent(match[2]),revision=field_revision(sql,ticket_id,field);return {revision,read:()=>workspace_field(sql,ticket_id,field)};}
    const row=sql.get<{manager_id:string|null;project_id:string|null}>('SELECT t.manager_id,CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END project_id FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id WHERE t.id=?',[ticket_id]);
    if(!row)throw new PlannerError('not_found','ticket not found',{ticket_id});
    const dependencies:[string,string][]=[['ticket',ticket_id],['blockers',ticket_id]];
    if(row.manager_id!==null)dependencies.push(['manager',row.manager_id]);
    if(row.project_id!==null)dependencies.push(['project',row.project_id]);
    dependencies.push(...sql.all<{blocking_ticket_id:string}>('SELECT blocking_ticket_id FROM ticket_blocks WHERE blocked_ticket_id=? ORDER BY blocking_ticket_id',[ticket_id]).map(r=>['ticket',r.blocking_ticket_id] as [string,string]));
    const revision=JSON.stringify([dependencies.map(([kind,id])=>[kind,id,String(sql.get<{revision:number}>('SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?',['workspace-'+kind,id])?.revision??0)])]);
    return {revision,read:()=>workspace_ticket(sql,ticket_id)};
  }
}
