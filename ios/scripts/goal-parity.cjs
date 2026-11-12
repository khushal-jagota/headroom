// Evaluate the production web presentation functions as the independent oracle.
// Output stays in the caller's scratch directory; no checked-in golden copies.
const fs = require('fs');
const ts = require(process.cwd() + '/web/node_modules/typescript');
const moduleFor = path => { const output=ts.transpileModule(fs.readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText; const m={exports:{}}; new Function('exports','module',output)(m.exports,m); return m.exports; };
const p=moduleFor('web/src/lib/charts/presentation.ts');
// The web removed its sample page on 2026-11-04. The native checks keep a copy of its catalogue.
const catalog=moduleFor('ios/scripts/goal-component-examples.ts').goalComponentExamples;
const cases=[];
for(const example of catalog) for(const sample of example.samples) {
const block=sample.block; if(block.type!=='chart'||block.kind==='binary') continue;
for(const range of block.ranges?.map(r=>r.id)??['all']) {
const data=p.chartData(block,[],range);
for(const active of [null,data[0]?.key].filter(x=>x!==undefined)) {
const h=p.chartHeadline(block,data,active,undefined,range);
cases.push({name:`${example.key}/${sample.id}/${range}/${active??'summary'}`,block,range,active,data,headline:h,tone:p.chartTone(block,h.value,h.target),comparison:p.comparisonText(block,h.value,h.target)});
}
}}
const geometry=moduleFor('web/src/lib/charts/lib/line-geometry.ts');
const curves=[];
for(const values of [[0,8,4,12], [3,null,5,10], [2,6,4], [5]]) for(const smooth of [true,false]) {
const positions=values.map((_,i)=>i/Math.max(1,values.length-1));
const breaks=values.map((_,i)=>i===2);
const shape=geometry.segmentedCurve(values.map(v=>v??NaN),positions,breaks,smooth);
curves.push({values,positions,breaks,smooth,samples:Array.from({length:241},(_,i)=>geometry.valueAt(shape,i/240))});
}
fs.writeFileSync(process.argv[2], JSON.stringify({cases,curves}));
console.log(cases.length+' native/web catalogue cases generated');
