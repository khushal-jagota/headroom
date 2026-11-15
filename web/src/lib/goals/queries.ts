import {versionedJsonQuery} from '../queryCatalogue';
import type {GoalsResponse,GoalResponse,GoalReadingsResponse} from './contracts';
export const goalQueries={
 list:()=>versionedJsonQuery<GoalsResponse>(['goals'],'/api/goals'),
 detail:(id:string)=>versionedJsonQuery<GoalResponse>(['goals',id],`/api/goals/${encodeURIComponent(id)}`),
 readings:(id:string)=>versionedJsonQuery<GoalReadingsResponse>(['goals',id,'readings'],`/api/goals/${encodeURIComponent(id)}/readings`)
};
