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
