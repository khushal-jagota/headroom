import type {GoalResponse} from './contracts';
export function goalsForHolder<T extends Pick<GoalResponse,'holder_kind'|'holder_id'>>(goals:readonly T[],holder:{kind:'chief'|'manager';id:string|null}){
 const held=goals.filter(goal=>goal.holder_kind===holder.kind&&(holder.kind==='chief'||goal.holder_id===holder.id));
 return {held,sole:held.length===1?held[0]:null};
}
