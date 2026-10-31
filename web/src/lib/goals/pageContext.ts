import type {PageContext} from '../pageKit/context';
import type {GoalReadingsResponse,GoalResponse} from './contracts';
import type {createGoalRequestSessions} from './requestSessions';

/** Keep accepted values live while each host retains its identity and capabilities. */
export function createGoalPageContext(source:{
 goalId:()=>string;
 goal:()=>GoalResponse|null|undefined;
 readings:()=>GoalReadingsResponse|null|undefined;
 narrow:()=>boolean;
 sessions:()=>ReturnType<typeof createGoalRequestSessions>;
},capabilities:Pick<PageContext,'updateBlock'|'manualSession'|'authorText'>={}):PageContext{
 return {
  get goalId(){return source.goalId();},
  get revision(){return source.goal()?.revision??0;},
  get narrow(){return source.narrow();},
  get values(){return source.readings()?.values??[];},
  get charts(){return source.readings()?.charts;},
  get completed(){return source.goal()?.completed??false;},
  get status(){return source.goal()?.status??'neutral';},
  get statusDate(){return source.goal()?.status_date;},
  checklistSession:(blockId,itemId)=>source.sessions().checklist(source.goalId(),blockId,itemId),
  checklistDeadlineSession:(blockId,itemId)=>source.sessions().checklistDeadline(source.goalId(),blockId,itemId),
  ...capabilities,
 };
}
