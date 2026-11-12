import type {GoalResponse} from './contracts';
const rank={bad:0,attention:1,good:2,neutral:3};
export function worstGoal(goals:readonly GoalResponse[]):GoalResponse|null{
 return goals.filter(goal=>goal.status&&goal.status!=='neutral').reduce<GoalResponse|null>((worst,goal)=>!worst||rank[goal.status]<rank[worst.status]?goal:worst,null);
}
export function goalTagLabel(status:GoalResponse['status']):string{return status==='bad'?'Off track':status==='attention'?'At risk':'On track';}
