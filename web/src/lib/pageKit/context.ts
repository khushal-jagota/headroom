import type {ManualMeasurementAdapter} from '../goals/manualMeasurements';
import type {ChecklistCommand,RequestSession} from '../goals/requestSessions';
import type {ManualWriteBody} from '../goals/contracts';
import type {GoalReadingsResponse,MeasureValue,Block,GoalResponse} from '../goals/contracts';
export const PAGE_CONTEXT=Symbol('page-document');
export type PageContext={goalId:string;revision:number;narrow:boolean;values:MeasureValue[];charts?:GoalReadingsResponse['charts'];completed:boolean;status:GoalResponse['status'];manualAdapter?:(measureId:string)=>ManualMeasurementAdapter;manualSession?:(measureId:string)=>RequestSession<ManualWriteBody>;checklistSession?:(blockId:string,itemId:string)=>RequestSession<ChecklistCommand>;updateBlock?:(id:string,update:(block:Block)=>Block,revision?:number)=>Promise<unknown>};
