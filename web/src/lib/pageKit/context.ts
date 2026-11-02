import type {ManualMeasurementAdapter} from '../goals/manualMeasurements';
import type {ChecklistCommand,ChecklistDeadlineSession,ManualWriteCommand,RequestSession} from '../goals/requestSessions';
import type {GoalReadingsResponse,MeasureValue,Block,GoalResponse} from '../goals/contracts';
import type {InlineEditDraft} from '../inlineEditDraft';
export const PAGE_CONTEXT=Symbol('page-document');
export type PageTextDraft=InlineEditDraft & {save?:(update:(block:Block)=>Block)=>Promise<unknown>;apply?:(block:Block,raw:string)=>Block};
export type PageContext={goalId:string;revision:number;narrow:boolean;values:MeasureValue[];charts?:GoalReadingsResponse['charts'];completed:boolean;status:GoalResponse['status'];statusDate?:string;manualAdapter?:(measureId:string)=>ManualMeasurementAdapter;manualSession?:(measureId:string)=>RequestSession<ManualWriteCommand>;checklistSession?:(blockId:string,itemId:string)=>RequestSession<ChecklistCommand>;checklistDeadlineSession?:(blockId:string,itemId:string)=>ChecklistDeadlineSession;updateBlock?:(id:string,update:(block:Block)=>Block,revision?:number)=>Promise<unknown>;authorText?:{draft:(blockId:string,field:string)=>PageTextDraft;begin:(blockId:string)=>(update:(block:Block)=>Block)=>Promise<unknown>}};
