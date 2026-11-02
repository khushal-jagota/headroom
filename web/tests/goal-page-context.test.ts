import {expect,it} from 'vitest';
import type {GoalReadingsResponse,GoalResponse,MeasureValue} from '../src/lib/goals/contracts';
import {createGoalPageContext} from '../src/lib/goals/pageContext';
import {createGoalRequestSessions} from '../src/lib/goals/requestSessions';
import type {PageContext} from '../src/lib/pageKit/context';

function goal(id:string):GoalResponse{
 return {id,project_id:'project',title:id,why:'',kind:'binary',active:true,completed:false,primary_checklist_block_id:null,deadline:null,primary_chart:null,
  page:{kind:'headroom.page',version:1,title:id,summary:'',blocks:[]},revision:1,created_at:0,updated_at:0,employee_backend:'codex',employee_launch_model:'model',employee_launch_reasoning_effort:null,conversation_id:null,status:'neutral',status_date:'2026-10-31',next_refresh_at:1};
}
function readings(id:string,value:number):GoalReadingsResponse{
 const reading:MeasureValue={measure_id:'measure',date:'2026-10-31',definition_revision:1,status:'value',value,inputs:[],missing_inputs:[],query_start_at:null,query_end_at:null,limitation:null,coverage_status:'not_applicable',coverage_reasons:[]};
 return {goal_id:id,revision:1,status:'neutral',status_date:'2026-10-31',next_refresh_at:1,values:[reading],sources:[],charts:{}};
}

it('reads replacement accepted goal and readings values without replacing the context',()=>{
 let accepted:GoalResponse|undefined,projection:GoalReadingsResponse|undefined,narrow=false;
 const sessions=createGoalRequestSessions();
 const context=createGoalPageContext({goalId:()=> 'first',goal:()=>accepted,readings:()=>projection,narrow:()=>narrow,sessions:()=>sessions});
 expect({revision:context.revision,values:context.values,charts:context.charts,completed:context.completed,status:context.status,date:context.statusDate,narrow:context.narrow}).toEqual({revision:0,values:[],charts:undefined,completed:false,status:'neutral',date:undefined,narrow:false});
 accepted=goal('first');projection=readings('first',3);
 expect(context.revision).toBe(1);
 expect(context.values).toBe(projection.values);
 expect(context.charts).toBe(projection.charts);
 accepted={...accepted,revision:2,completed:true,status:'good',status_date:'2026-11-01'};
 projection=readings('first',8);narrow=true;
 expect({revision:context.revision,completed:context.completed,status:context.status,date:context.statusDate,narrow:context.narrow}).toEqual({revision:2,completed:true,status:'good',date:'2026-11-01',narrow:true});
 expect(context.values).toBe(projection.values);
 expect(context.values[0].value).toBe(8);
 expect(context.charts).toBe(projection.charts);
});

it('routes captured and reactive identities through the current session registry',()=>{
 let id='first',sessions=createGoalRequestSessions();
 const capturedId=id;
 const source={goal:()=>goal(id),readings:()=>undefined,narrow:()=>false,sessions:()=>sessions};
 const captured=createGoalPageContext({...source,goalId:()=>capturedId});
 const reactive=createGoalPageContext({...source,goalId:()=>id});
 const first=captured.checklistSession!('steps','item');
 const firstDeadline=captured.checklistDeadlineSession!('steps','item');
 expect(reactive.checklistSession!('steps','item')).toBe(first);
 expect(reactive.checklistDeadlineSession!('steps','item')).toBe(firstDeadline);
 id='second';
 expect(captured.goalId).toBe('first');
 expect(reactive.goalId).toBe('second');
 expect(captured.checklistSession!('steps','item')).toBe(first);
 expect(captured.checklistDeadlineSession!('steps','item')).toBe(firstDeadline);
 expect(reactive.checklistSession!('steps','item')).toBe(sessions.checklist('second','steps','item'));
 expect(reactive.checklistDeadlineSession!('steps','item')).toBe(sessions.checklistDeadline('second','steps','item'));
 sessions=createGoalRequestSessions();
 expect(captured.checklistSession!('steps','item')).toBe(sessions.checklist('first','steps','item'));
 expect(captured.checklistSession!('steps','item')).not.toBe(first);
 expect(captured.checklistDeadlineSession!('steps','item')).toBe(sessions.checklistDeadline('first','steps','item'));
 expect(captured.checklistDeadlineSession!('steps','item')).not.toBe(firstDeadline);
 expect(reactive.checklistSession!('steps','item')).toBe(sessions.checklist('second','steps','item'));
 expect(reactive.checklistDeadlineSession!('steps','item')).toBe(sessions.checklistDeadline('second','steps','item'));
});

it('retains optional host capability references without granting absent capabilities',()=>{
 const sessions=createGoalRequestSessions();
 const source={goalId:()=> 'first',goal:()=>goal('first'),readings:()=>undefined,narrow:()=>true,sessions:()=>sessions};
 const updateBlock:PageContext['updateBlock']=async()=>undefined;
 const manualSession:PageContext['manualSession']=measureId=>sessions.manual('first',measureId);
 const authorText:PageContext['authorText']={draft:()=>({value:null,error:null,saving:false}),begin:()=>async()=>undefined};
 const card=createGoalPageContext(source,{updateBlock});
 expect(card.updateBlock).toBe(updateBlock);
 expect('manualSession' in card).toBe(false);
 expect('authorText' in card).toBe(false);
 const workspace=createGoalPageContext(source,{updateBlock,manualSession,authorText});
 expect(workspace.updateBlock).toBe(updateBlock);
 expect(workspace.manualSession).toBe(manualSession);
 expect(workspace.authorText).toBe(authorText);
 expect(workspace.manualSession!('measure')).toBe(sessions.manual('first','measure'));
});
