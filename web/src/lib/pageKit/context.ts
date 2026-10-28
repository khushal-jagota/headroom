import type {MeasureValue,Block,GoalResponse} from '../goals/contracts';
export const PAGE_CONTEXT=Symbol('page-document');
export type PageContext={goalId:string;revision:number;narrow:boolean;values:MeasureValue[];completed:boolean;status:GoalResponse['status'];updateBlock?:(id:string,update:(block:Block)=>Block,revision?:number)=>Promise<unknown>};
