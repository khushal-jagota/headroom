import {describe,it,expect} from 'vitest';
import {chartData,formatReading,inspectedReading} from '../src/lib/charts/presentation';
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
