import {versionedJsonQuery,jsonQuery} from '../queryCatalogue';
import type {GoalsResponse,GoalResponse,GoalReadingsResponse} from './contracts';
import type {ConversationStartValues} from '../conversation/wire';
export const goalQueries={
 list:()=>versionedJsonQuery<GoalsResponse>(['goals'],'/api/goals'),
 detail:(id:string)=>versionedJsonQuery<GoalResponse>(['goals',id],`/api/goals/${encodeURIComponent(id)}`),
 readings:(id:string)=>versionedJsonQuery<GoalReadingsResponse>(['goals',id,'readings'],`/api/goals/${encodeURIComponent(id)}/readings`),
 start:(id:string)=>jsonQuery<ConversationStartValues>(['goals',id,'conversation-start-values'],`/api/goals/${encodeURIComponent(id)}/conversation/start-values`)
};
