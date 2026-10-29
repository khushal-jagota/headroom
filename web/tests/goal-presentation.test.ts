import {describe,it,expect} from 'vitest';
import {chartData,chartHeadline,chartTone,comparisonText,missingReadingText,formatReading,inspectedReading} from '../src/lib/charts/presentation';
import {updatePageBlock} from '../src/lib/goals/presentation';
import type {ChartBlock,MeasureValue,Block} from '../src/lib/goals/contracts';
const chart:ChartBlock={type:'chart',id:'balance',kind:'line',placement:'primary',measure_id:'balance',target:0,direction:'atleast',format:{prefix:'€',decimals:2}};
const value=(date:string,quantity:number|null):MeasureValue=>({date,measure_id:'balance',value:quantity,definition_revision:1,status:quantity===null?'missing_input':'value',inputs:[],missing_inputs:[]});
describe('goal charts',()=>{
 it('keeps signed fractional corrections, final gaps and dated reference paths',()=>{
  const data=chartData({...chart,reference:[{date:'2026-10-01',value:-10},{date:'2026-10-03',value:10}]},[value('2026-10-01',-4.25),value('2026-10-02',-8.5),value('2026-10-03',null)],'all');
  expect(data.map(d=>d.values.actual)).toEqual([-4.25,-8.5,undefined]);
  expect(data.map(d=>d.values.target)).toEqual([-10,0,10]);
  expect(data.map(d=>d.position)).toEqual([0,.5,1]);
  expect(formatReading(chart,-8.5)).toBe('€-8.50');
  expect(formatReading(chart,NaN)).toBe('No reading');
 });
 it('selects the week by dates rather than number of observations and respects static ranges',()=>{
  const values=[value('2026-09-30',20),value('2026-10-01',1),value('2026-10-07',null)];
  expect(chartData(chart,values,'week').map(d=>d.key)).toEqual(['2026-10-01','2026-10-07']);
  const staticChart:ChartBlock={...chart,measure_id:undefined,ranges:[{id:'previous',label:'Previous',series:[{name:'Balance',points:[{label:'A',value:-1},{label:'B',value:null}]}]}]};
  expect(chartData(staticChart,[],'previous').map(d=>d.values.actual)).toEqual([-1,undefined]);
 });
});
it('edits a nested checklist by stable identity after reorder without replacing neighboring blocks',()=>{
 const blocks:Block[]=[{type:'section',id:'approach',title:'Approach',blocks:[{type:'text',id:'note',text:'Keep this'},{type:'checklist',id:'steps',items:[{id:'second',label:'Second',state:'open'},{id:'first',label:'First',state:'done'}]}]}];
 const updated=updatePageBlock(blocks,'steps',b=>b.type==='checklist'?{...b,items:b.items.map(i=>i.id==='second'?{...i,state:'done'}:i)}:b);
 expect(updated[0]).toEqual({...blocks[0],blocks:[{type:'text',id:'note',text:'Keep this'},{type:'checklist',id:'steps',items:[{id:'second',label:'Second',state:'done'},{id:'first',label:'First',state:'done'}]}]});
 expect((blocks[0] as Extract<Block,{type:'section'}>).blocks?.[1]).toMatchObject({items:[{state:'open'},{state:'done'}]});
});

import {emptyFilters,goalMatches} from '../src/lib/goals/filters';
import type {GoalResponse} from '../src/lib/goals/contracts';
it('filters active completed goals independently and accepts signed numeric thresholds',()=>{
 const goal={title:'Keep delivery ready',active:true,completed:true,project_id:'p',deadline:'2026-10-31',primary_chart:chart} as GoalResponse;
 expect(goalMatches(goal,-8.5,{...emptyFilters(),state:'active',completion:'done',current:{op:'lte',value:'-5'}})).toBe(true);
 expect(goalMatches({...goal,active:false},-8.5,{...emptyFilters(),state:'active'})).toBe(false);
 expect(goalMatches(goal,undefined,{...emptyFilters(),current:{op:'missing',value:''}})).toBe(true);
});

it('filters selected checklists in saved item units instead of chart values or percentages',()=>{
 const checklist:Extract<Block,{type:'checklist'}>={type:'checklist',id:'steps',items:[{id:'first',label:'First',state:'done'},{id:'second',label:'Second',state:'done'},{id:'blocked',label:'Blocked',state:'blocked'}]};
 const goal={title:'Delivery',kind:'binary',active:true,completed:false,primary_checklist_block_id:'steps',page:{kind:'headroom.page',version:1,title:'Delivery',summary:'',blocks:[checklist]}} as GoalResponse;
 const filters={...emptyFilters(),current:{op:'eq' as const,value:'2'},target:{op:'eq' as const,value:'3'}};
 expect(goalMatches(goal,66.67,filters,{target:100})).toBe(true);
 expect(goalMatches(goal,66.67,{...filters,current:{op:'eq',value:'66.67'}})).toBe(false);
 const reopened={...goal,page:{...goal.page,blocks:[{...checklist,items:checklist.items.map(item=>({...item,state:'open' as const}))}]}};
 expect(goalMatches(reopened,undefined,{...filters,current:{op:'eq',value:'0'}})).toBe(true);
 const empty={...goal,page:{...goal.page,blocks:[{...checklist,items:[]}]}};
 expect(goalMatches(empty,undefined,{...filters,current:{op:'eq',value:'0'},target:{op:'eq',value:'0'}})).toBe(true);
 expect(goalMatches({...goal,primary_checklist_block_id:null},undefined,{...emptyFilters(),current:{op:'missing',value:''},target:{op:'missing',value:''}})).toBe(true);
});

it('inspection follows a corrected dated value and a corrected gap without a pointer move',()=>{
 const before=chartData(chart,[value('2026-10-01',4),value('2026-10-02',5)],'all');
 const key=before[0].key;
 expect(inspectedReading(before,key)?.values.actual).toBe(4);
 const corrected=chartData(chart,[value('2026-10-01',-2.5),value('2026-10-02',5)],'all');
 expect(inspectedReading(corrected,key)?.values.actual).toBe(-2.5);
 const missing=chartData(chart,[value('2026-10-01',null),value('2026-10-02',5)],'all');
 expect(inspectedReading(missing,key)?.values.actual).toBeUndefined();
});


it('preserves sum and composition semantics across ranges, gaps, and inspection',()=>{
 const aggregate:ChartBlock={...chart,measure_id:undefined,aggregate:'sum',target:3,series:[{name:'Teams',points:[{label:'A',value:2},{label:'B',value:null},{label:'C',value:4}]}]};
 const data=chartData(aggregate,[],'all');
 expect(chartHeadline(aggregate,data)).toMatchObject({value:6,target:9,missingCount:1});
 expect(chartHeadline(aggregate,data,'C')).toMatchObject({value:4,target:3});
 expect(chartHeadline(aggregate,data,'B').value).toBeUndefined();
 expect(chartHeadline(aggregate,chartData({...aggregate,series:[{name:'Teams',points:[{label:'A',value:null}]}]},[],'all')).value).toBeUndefined();
 const composition:ChartBlock={type:'chart',id:'plans',kind:'bar',placement:'primary',stacked:true,series:[{name:'Business',points:[{label:'Jul',value:600},{label:'Aug',value:800}]},{name:'Team',points:[{label:'Jul',value:1300},{label:'Aug',value:1500}]},{name:'Pro',points:[{label:'Jul',value:1600},{label:'Aug',value:1900}]}]};
 expect(chartHeadline(composition,chartData(composition,[],'all')).value).toBe(4200);
 expect(chartHeadline(composition,chartData(composition,[],'all'),'Jul').value).toBe(3500);
 const missing={...composition,series:[...composition.series!.slice(0,2),{name:'Pro',points:[{label:'Jul',value:1600}]}]};
 expect(chartHeadline(missing,chartData(missing,[],'all')).value).toBeUndefined();
 const emptyCategory={...composition,series:[...composition.series!,{name:'Unrecorded',points:[]}]};
 expect(chartHeadline(emptyCategory,chartData(emptyCategory,[],'all')).value).toBeUndefined();
 const ranged:ChartBlock={...aggregate,aggregate:undefined,target:10,ranges:[{id:'week',label:'Week',target:2,series:[{name:'Teams',points:[{label:'Mon',value:1}]}]},{id:'all',label:'All time',target:12,series:aggregate.series!}]};
 expect(chartHeadline(ranged,chartData(ranged,[],'week'))).toMatchObject({value:1,target:2});
 expect(chartHeadline(ranged,chartData(ranged,[],'all'))).toMatchObject({value:4,target:12});
});

it('reports approved threshold, progress, budget, and missing-reading states without inventing values',()=>{
 const floor:ChartBlock={...chart,format:{decimals:2},unit:'%',target_label:'floor',difference_unit:'pp'};
 expect(comparisonText(floor,99.72,99.5)).toBe('0.22 pp above floor');
 expect(comparisonText(floor,99.36,99.5)).toBe('0.14 pp below floor');
 expect(chartTone({...chart,below_target_tone:'attention'},4,5)).toBe('attention');
 expect(chartTone({...chart,reference:[{date:'2026-10-01',value:5}]},4,5)).toBe('bad');
 expect(comparisonText({...chart,kind:'progress'},12,12)).toBe('Target reached');
 expect(comparisonText({...chart,kind:'progress',format:undefined},14,12)).toBe('2 beyond target');
 expect(chartTone({...chart,kind:'time-budget',direction:undefined},3,8)).toBe('good');
 expect(chartTone({...chart,kind:'time-budget',direction:undefined},10,8)).toBe('bad');
 const gap=chartData(chart,[value('2026-10-01',4),value('2026-10-02',null)],'all');
 expect(missingReadingText(gap,'2026-10-02')).toBe('No reading for 2026-10-02');
 expect(missingReadingText(chartData(chart,[value('2026-10-02',null)],'all'))).toBe('No readings yet');
 expect(chartHeadline({...chart,kind:'time-budget'},chartData(chart,[value('2026-10-02',-1)],'all')).value).toBeUndefined();
 const goal={title:'New teams',primary_chart:{...chart,target:3},active:true,completed:false} as GoalResponse;
 expect(goalMatches(goal,6,{...emptyFilters(),target:{op:'eq',value:'9'}},{target:9})).toBe(true);
 expect(goalMatches(goal,6,{...emptyFilters(),target:{op:'missing',value:''}},{target:undefined})).toBe(true);
});

import type {ScalarChartProjection,ScalarChartPoint} from '../src/lib/goals/contracts';
const formula:ChartBlock={...chart,expectation:{op:'constant',value:5},reference:[{date:'2026-10-29',value:999}]};
const point=(date:string,actual:number|null,expected:number|null,break_before=false):ScalarChartPoint=>({
 date,actual:value(date,actual),expected:{status:expected===null?'missing_input':'value',value:expected,inputs:[],missing_inputs:[]},
 difference:actual===null||expected===null?null:actual-expected,meets_expectation:actual===null||expected===null?null:actual>=expected,break_before,
});
const projected=(points:ScalarChartPoint[],summary_date:string|null='2026-10-29'):ScalarChartProjection=>({kind:'scalar',expected_source:'expectation',today:'2026-10-29',first_date:points[0].date,last_date:points.at(-1)!.date,summary_date,points});

it('uses measure-local today for formula windows and keeps future targets out of the default headline',()=>{
 const projection=projected(Array.from({length:43},(_,index)=>{
  const date=new Date(Date.UTC(2026,8,20+index)).toISOString().slice(0,10);
  return point(date,date==='2026-10-29'||date>'2026-10-29'?null:4,5,date==='2026-10-28');
 }));
 const week=chartData(formula,[],'week',projection),month=chartData(formula,[],'month',projection),all=chartData(formula,[],'all',projection);
 expect(week.map(item=>item.key)).toEqual(['2026-10-23','2026-10-24','2026-10-25','2026-10-26','2026-10-27','2026-10-28','2026-10-29']);
 expect(month).toHaveLength(30);
 expect(all.at(-1)?.key).toBe('2026-11-01');
 expect(all.find(item=>item.key==='2026-10-28')?.breakBefore).toEqual({target:true});
 const current=chartHeadline(formula,all,null,projection);
 expect(current).toMatchObject({value:undefined,target:5,label:'2026-10-29'});
 expect(chartTone(formula,current.value,current.target,current.point)).toBe('neutral');
 const inspected=chartHeadline(formula,all,'2026-10-28',projection);
 expect(inspected).toMatchObject({value:4,target:5,label:'2026-10-28',point:{difference:-1,meets_expectation:false}});
 expect(comparisonText(formula,inspected.value,inspected.target,inspected.point)).toBe('€1.00 below target');
 expect(chartTone(formula,100,0,inspected.point)).toBe('bad'); // The server comparison owns the dated tone.
 const precise=point('2026-10-28',4.99999,5);
 expect(comparisonText(formula,precise.actual.value!,precise.expected.value!,precise)).toBe('€0.00 below target');
 expect(chartTone(formula,precise.actual.value!,precise.expected.value!,precise)).toBe('bad');
 const corrected=projected(projection.points.map(item=>item.date==='2026-10-28'?point(item.date,6,5):item));
 expect(chartHeadline(formula,chartData(formula,[],'all',corrected),'2026-10-28',corrected)).toMatchObject({value:6,point:{difference:1,meets_expectation:true}});
});

it('never substitutes legacy targets for pending, invalid, or future-only formula projections',()=>{
 expect(chartData(formula,[value('2026-10-29',10)],'all')).toEqual([]);
 expect(chartHeadline(formula,[])).toMatchObject({value:undefined,target:undefined});
 expect(chartTone(formula,10,0)).toBe('neutral');
 expect(comparisonText(formula,10,0)).toBe('');
 const failed=projected([point('2026-10-29',10,null)]);
 const data=chartData(formula,[],'all',failed);
 expect(data[0].values).toEqual({actual:10,target:undefined});
 expect(data[0].comparisons).toEqual({actual:null});
 expect(chartTone(formula,10,0,failed.points[0])).toBe('neutral');
 const future=projected([point('2026-10-30',null,20)],null);
 expect(chartHeadline(formula,chartData(formula,[],'all',future),null,future)).toMatchObject({value:undefined,target:undefined,label:undefined});
 expect(chartHeadline(formula,chartData(formula,[],'all',future),'2026-10-30',future)).toMatchObject({value:undefined,target:20,label:'2026-10-30'});
});
