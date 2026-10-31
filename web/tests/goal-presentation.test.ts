import {describe,it,expect} from 'vitest';
import {chartData,chartHeadline,chartTone,comparisonText,missingReadingText,readingSuffix,formatReading,formatTrimmedReading,formatAxisTick,inspectedReading} from '../src/lib/charts/presentation';
import {updatePageBlock,deadlineTone,deadlineColor} from '../src/lib/goals/presentation';
import type {ChartBlock,MeasureValue,Block} from '../src/lib/goals/contracts';
const chart:ChartBlock={type:'chart',id:'balance',kind:'line',placement:'primary',measure_id:'balance',target:0,direction:'atleast',format:{prefix:'€',decimals:2}};
const value=(date:string,quantity:number|null):MeasureValue=>({date,measure_id:'balance',value:quantity,definition_revision:1,status:quantity===null?'missing_input':'value',inputs:[],missing_inputs:[],query_start_at:null,query_end_at:null,limitation:null,coverage_status:'not_applicable',coverage_reasons:[]});
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
 date,actual:value(date,actual),expected:{status:expected===null?'missing_input':'value',value:expected,inputs:[],missing_inputs:[],coverage_status:'not_applicable',coverage_reasons:[]},
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

import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {GoalChart} from '../src/lib/charts/GoalChart';
import {Gauge} from '../src/lib/charts/components/gauge';
import {gaugeArc,gaugePoint} from '../src/lib/charts/lib/gauge-geometry';
import {observationDetails} from '../src/lib/charts/presentation';

it('preserves the dial endpoints, bounded sweep, and target coordinates across both path precisions',()=>{
 const empty='M 20.302 79.698 A 42 42 0 0 1 20.302 79.698';
 const full='M 20.302 79.698 A 42 42 0 1 1 79.698 79.698';
 expect(gaugeArc(-1,3)).toBe(empty);
 expect(gaugeArc(0,3)).toBe(empty);
 expect(gaugeArc(.5,3)).toBe('M 20.302 79.698 A 42 42 0 0 1 50.000 8.000');
 expect(gaugeArc(1,3)).toBe(full);
 expect(gaugeArc(2,3)).toBe(full);
 expect(gaugeArc(1)).toContain('20.301515190165');
 expect(gaugeArc(1)).not.toBe(full);
 expect(gaugePoint(.5,35)).toEqual({x:50,y:15});
 expect(gaugePoint(.5,49)).toEqual({x:50,y:1});
});

it('retains read-only meter bounds, target marker and formatted inspection rows',()=>{
 const render=(reading:number,target=50)=>renderToStaticMarkup(createElement(Gauge,{
  label:'Distance',value:reading,min:0,max:100,unit:' m',formatValue:number=>`${number} m`,
  target:{value:target,label:`${target} m target`},
 }));
 const markup=render(150);
 const meter=markup.match(/<svg[^>]*role="meter"[^>]*>/)?.[0];
 expect(meter).toContain('aria-label="Distance"');
 expect(meter).toContain('aria-valuemin="0"');
 expect(meter).toContain('aria-valuemax="100"');
 expect(meter).toContain('aria-valuenow="100"');
 expect(meter).toContain('aria-valuetext="150 m, 50 m target"');
 expect(meter).toContain('focusable="false"');
 expect(markup).toContain('data-gauge-target="50"');
 expect(markup.match(/d="M 50 15 L 50 1"/g)).toHaveLength(2);
 expect(markup).toMatch(/<div[^>]*class="[^"]*readout[^"]*"[^>]*aria-hidden="true"/);
 expect(markup).toMatch(/data-tooltip-series="actual"[\s\S]*?>Current<[^]*?>150 m</);
 expect(markup).toMatch(/data-tooltip-series="target"[^]*?data-dashed="true"[^]*?>Target<[^]*?>50 m</);
 expect(render(-5)).toContain('aria-valuenow="0"');
 expect(render(25,120)).not.toContain('data-gauge-target=');
});

it('compares retained scalar values through reference paths, sums and corrected inspection regardless of coverage',()=>{
 const partial:MeasureValue={...value('2026-10-28',0),coverage_status:'partial',coverage_reasons:['The query covers part of the date.'],query_start_at:Date.parse('2026-10-28T06:11:26Z')/1000,query_end_at:Date.parse('2026-10-29T00:00:00Z')/1000};
 const unknown:MeasureValue={...value('2026-10-29',8),coverage_status:'unknown',coverage_reasons:['No query bounds are supplied.']};
 const complete:MeasureValue={...value('2026-10-27',6),coverage_status:'complete'};
 const target:ChartBlock={...chart,target:5,reference:[{date:'2026-10-27',value:5},{date:'2026-10-29',value:5}]};
 const data=chartData(target,[complete,partial,unknown],'week');
 expect(data.map(item=>item.values.actual)).toEqual([6,0,8]);
 expect(data.map(item=>item.values.target)).toEqual([5,5,5]);
 expect(data.map(item=>item.comparisons?.actual)).toEqual([true,false,true]);
 const selected=chartHeadline(target,data,'2026-10-28');
 expect(selected).toMatchObject({value:0,target:5,coverage_status:'partial'});
 expect(observationDetails(selected).join(' ')).toContain('2026-10-28T06:11:26.000Z');
 for(const direction of ['atleast','atmost'] as const){
  expect(chartTone({...target,direction},selected.value,selected.target,undefined,undefined,selected.coverage_status)).toBe(direction==='atmost'?'good':'bad');
 }
 expect(comparisonText(target,0,5,undefined,undefined,selected.coverage_status)).toBe('€5.00 below target');
 const total=chartHeadline({...target,aggregate:'sum'},data);
 expect(total).toMatchObject({value:14,target:15,coverage_status:'partial'});
 const missing=chartHeadline({...target,aggregate:'sum'},chartData(target,[complete,{...value('2026-10-28',null),coverage_status:'complete'}],'all'));
 expect(missing).toMatchObject({value:6,target:10,coverage_status:'unknown'});
 const corrected=chartData(target,[complete,{...partial,coverage_status:'complete'},unknown],'all');
 expect(chartHeadline(target,corrected,'2026-10-28').coverage_status).toBe('complete');
 expect(comparisonText(target,8,5,undefined,undefined,'unknown')).toBe('€3.00 above target');
 expect(chartTone(target,8,5,undefined,undefined,'not_applicable')).toBe('good');
});

it('preserves formula verdicts with unknown actual or expected source coverage',()=>{
 const sample=point('2026-10-29',8,5);
 sample.expected={...sample.expected,coverage_status:'unknown',coverage_reasons:['The comparison period lacks coverage.']};
 const projection=projected([sample]);
 const data=chartData(formula,[],'all',projection),current=chartHeadline(formula,data,null,projection);
 expect(data[0].comparisons).toEqual({actual:true});
 expect(current.coverage_status).toBe('unknown');
 expect(chartTone(formula,8,5,current.point)).toBe('good');
 expect(comparisonText(formula,8,5,current.point)).toBe('€3.00 above target');
});

it('compares partial readings across number, progress, budget and gauge while preserving raw details',()=>{
 const partial:MeasureValue={...value('2026-10-29',8),coverage_status:'partial',coverage_reasons:['Only part of this date was queried.'],query_start_at:Date.parse('2026-10-29T06:00:00Z')/1000,query_end_at:Date.parse('2026-10-29T21:04:10Z')/1000};
 for(const kind of ['number','progress','time-budget','gauge'] as const){
  const markup=renderToStaticMarkup(createElement(GoalChart,{block:{...chart,kind,target:5},values:[partial],narrow:false,preferenceKey:`scalar-${kind}`}));
  expect(markup).not.toContain('Partial reading');
  expect(markup).not.toContain('Partial difference:');
  expect(markup).toContain('Source coverage for 2026-10-29: partial.');
  expect(markup).toContain('2026-10-29T21:04:10.000Z');
  if(kind==='number'||kind==='progress')expect(markup).toContain('var(--accent-done)');
  if(kind==='gauge')expect(markup).toContain('On target');
  if(kind==='time-budget')expect(markup).toContain('Over budget');
 }
 const supporting=renderToStaticMarkup(createElement(GoalChart,{block:{...chart,kind:'number',placement:'supporting',target:5},values:[{...partial,coverage_status:'unknown'}],narrow:true,preferenceKey:'unknown-support'}));
 expect(supporting).not.toContain('Coverage unavailable');
 expect(supporting).toContain('€3.00 above target');
 expect(supporting).toContain('Source coverage for 2026-10-29: unknown.');
 expect(supporting).toContain('<summary>Observation details</summary>');
});


it('separates word units while preserving authored suffixes and numeric formatting',()=>{
 const units:ChartBlock={...chart,format:{decimals:2},unit:'exception events'};
 expect(formatReading(units,1234.5)).toBe('1,234.50 exception events');
 expect(formatTrimmedReading(units,1234)).toBe('1,234 exception events');
 expect(formatTrimmedReading(units,-1.25)).toBe('-1.25 exception events');
 expect(readingSuffix({...units,unit:'小时'})).toBe(' 小时');
 expect(readingSuffix({...units,unit:'\t hours'})).toBe('\t hours');
 expect(readingSuffix({...units,unit:' hours'})).toBe(' hours');
 expect(formatReading({...units,unit:'%'},2)).toBe('2.00%');
 expect(readingSuffix({...units,unit:''})).toBe('');
 expect(readingSuffix({...units,unit:undefined})).toBe('');
 for(const suffix of ['', 'h', 'k']) {
  const literal={...units,format:{decimals:1,suffix}};
  expect(formatReading(literal,2)).toBe(`2.0${suffix}`);
  expect(formatTrimmedReading(literal,2)).toBe(`2${suffix}`);
 }
 expect(formatAxisTick(units,1000)).toBe('1000');
 expect(formatAxisTick({...units,format:{prefix:'€',decimals:2}},2500)).toBe('€2.5k exception events');
 expect(formatAxisTick({...units,format:{prefix:'€',suffix:''}},2500)).toBe('€2.5k');
 expect(formatReading({...units,format:{prefix:'€',decimals:2}},-2.5)).toBe('€-2.50 exception events');
});


it('uses the supplied server date for deadline-only tones and gives saved completion precedence',()=>{
 const today='2026-10-30';
 expect(deadlineTone('2026-10-29',false,today)).toBe('bad');
 expect(deadlineTone('2026-10-30',false,today)).toBe('attention');
 expect(deadlineTone('2026-10-31',false,today)).toBe('neutral');
 expect(deadlineTone(null,false,today)).toBe('neutral');
 expect(deadlineTone(undefined,true,today)).toBe('neutral');
 expect(deadlineTone('2026-10-29',true,today)).toBe('good');
 expect(deadlineTone('2026-10-31',true,today)).toBe('good');
 // A browser calendar or its current date never replaces an absent server calendar.
 expect(deadlineTone('2026-10-29',true,undefined)).toBe('neutral');
 expect(deadlineTone('2026-10-30',false,'2026-10-31')).toBe('bad');
 expect(['bad','attention','good','neutral'].map(tone=>deadlineColor(tone as GoalResponse['status']))).toEqual([
  'var(--accent-error)','var(--accent-attention)','var(--accent-done)','var(--text-muted)',
 ]);
});
