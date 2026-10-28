import type { ChartBlock, MeasureValue } from '../goals/contracts';
import type { LineChartDatum } from './components/line-chart-integrated';
export type ChartRangeSelection = 'week' | 'month' | 'all' | string;
export function formatReading(block: ChartBlock, value: number): string {
  if (!Number.isFinite(value)) return "No reading";
  return `${block.format?.prefix ?? ''}${value.toLocaleString('en-US', {maximumFractionDigits:block.format?.decimals ?? 0, minimumFractionDigits:block.format?.decimals ?? 0})}${block.format?.suffix ?? block.unit ?? ''}`;
}
export function chartData(block: ChartBlock, values: MeasureValue[], range: ChartRangeSelection): LineChartDatum[] {
  const selected = block.ranges?.find(item => item.id === range)?.series ?? block.series ?? block.ranges?.[0]?.series ?? [];
  let data: LineChartDatum[];
  if (block.measure_id) {
    const actual=values.filter(v=>v.measure_id===block.measure_id).sort((a,b)=>a.date.localeCompare(b.date));
    const latest=actual.at(-1)?.date;
    const cutoff=latest ? Date.parse(latest)-(range==='week'?6:range==='month'?29:Infinity)*86400000 : -Infinity;
    data=actual.filter(v=>Date.parse(v.date)>=cutoff).map(v=>({key:v.date,label:v.date,axisLabel:new Date(v.date+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'}),values:{actual:v.value??undefined}}));
  } else {
    const points=new Map<string,LineChartDatum>();
    selected.forEach((series,index)=>series.points.forEach(p=>{
      const d=points.get(p.label)??{key:p.label,label:p.label,axisLabel:p.axis??p.label,values:{}};
      d.values[index===0?'actual':series.name]=p.value??undefined;points.set(p.label,d);
    })); data=[...points.values()];
  }
  const first=Date.parse(data[0]?.key??''),last=Date.parse(data.at(-1)?.key??'');
  return data.map(d=>({...d,...(Number.isFinite(first)&&last>first?{position:(Date.parse(d.key)-first)/(last-first)}:{}),values:{...d.values,...(block.reference?.length?{target:referenceAt(block,d.key)}:block.target!==undefined?{target:block.target}:{})}}));
}
function referenceAt(block: ChartBlock,date:string):number|undefined {
  const refs=[...(block.reference??[])].sort((a,b)=>a.date.localeCompare(b.date));
  const exact=refs.find(r=>r.date===date);if(exact)return exact.value;
  const before=refs.filter(r=>r.date<date).at(-1),after=refs.find(r=>r.date>date);
  if(!before||!after)return undefined;
  return before.value+(after.value-before.value)*(Date.parse(date)-Date.parse(before.date))/(Date.parse(after.date)-Date.parse(before.date));
}
export function readChartPreference(key:string):{range:string;expanded:boolean} {
  try {const v=JSON.parse(sessionStorage.getItem(key)??'null');return {range:typeof v?.range==='string'?v.range:'month',expanded:v?.expanded===true};}catch{return {range:'month',expanded:false};}
}

/** Inspection follows identity, so same-date corrections replace the displayed value. */
export function inspectedReading(data:LineChartDatum[],activeKey:string|null):LineChartDatum|undefined{return data.find(d=>d.key===activeKey)??data.at(-1);}
