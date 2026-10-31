import {expect,it} from 'vitest';
import type {ChartBlock,ChecklistBlock,GoalResponse,PageDocument} from '../src/lib/goals/contracts';
import {checklistCounts,pageWithoutSelectedChecklistForRender,savedChecklists,selectedChecklist} from '../src/lib/goals/checklistCompletion';
import {resolveGoalPrimaryDisplay} from '../src/lib/goals/primaryDisplay';
import {emptyFilters,goalMatches} from '../src/lib/goals/filters';

it('selects a saved nested checklist by ID and excludes only its render copy without losing authored content',()=>{
 const ordinary:ChecklistBlock={type:'checklist',id:'ordinary',title:'Keep this checklist',items:[]};
 const selected:ChecklistBlock={type:'checklist',id:'selected',title:'Delivery',items:[{id:'done',label:'Done',state:'done'},{id:'blocked',label:'Blocked',state:'blocked'},{id:'open',label:'Open',state:'open'}]};
 const note={type:'text' as const,id:'note',text:'Keep this authored neighbor'};
 const page:PageDocument={kind:'headroom.page',version:1,title:'Delivery',summary:'Keep this summary',blocks:[ordinary,{type:'section',id:'outer',title:'Approach',summary:'Keep section text',blocks:[{type:'section',id:'views',title:'Views',views:[{id:'plan',label:'Plan',blocks:[{type:'grid',id:'grid',columns:2,blocks:[{type:'segmented',id:'tabs',views:[{id:'steps',label:'Steps',blocks:[note,selected]},{id:'other',label:'Other',blocks:[]}]}]}]}]}]}]};
 const original=structuredClone(page);
 const rendered=pageWithoutSelectedChecklistForRender(page,'selected');
 const expected=structuredClone(page);
 const outer=expected.blocks[1];
 if(outer.type!=='section')throw new Error('Expected outer section');
 const section=outer.blocks![0];
 if(section.type!=='section')throw new Error('Expected section views');
 const grid=section.views![0].blocks[0];
 if(grid.type!=='grid')throw new Error('Expected grid');
 const tabs=grid.blocks[0];
 if(tabs.type!=='segmented')throw new Error('Expected segmented views');
 tabs.views[0].blocks=[note];
 expect(savedChecklists(page)).toEqual([ordinary,selected]);
 expect(selectedChecklist(page,'selected')).toBe(selected);
 expect(checklistCounts(selected)).toEqual({checked:1,total:3});
 expect(rendered).toEqual(expected);
 expect(savedChecklists(rendered)).toEqual([ordinary]);
 expect(page).toEqual(original);
 expect(selectedChecklist(page,'selected')).toBe(selected);
 const chart:ChartBlock={type:'chart',id:'legacy',kind:'number',placement:'primary',target:7};
 const goal={kind:'binary',title:'Delivery',active:true,completed:false,page,primary_checklist_block_id:'selected',primary_chart:chart} as GoalResponse;
 expect(resolveGoalPrimaryDisplay(goal)).toEqual({checklist:selected,chart:null});
 expect(resolveGoalPrimaryDisplay({...goal,primary_checklist_block_id:'ordinary'})).toEqual({checklist:ordinary,chart:null});
 expect(resolveGoalPrimaryDisplay({...goal,kind:'quantitative'})).toEqual({checklist:null,chart});
 const {kind,...legacy}=goal;
 expect(resolveGoalPrimaryDisplay(legacy as GoalResponse).chart).toBe(chart);
 expect(resolveGoalPrimaryDisplay({...goal,primary_checklist_block_id:null}).chart?.kind).toBe('binary');
 // Legacy numeric filter input remains the saved chart, never the rendered completion chart.
 expect(goalMatches({...goal,primary_checklist_block_id:null},undefined,{...emptyFilters(),target:{op:'eq',value:'7'},current:{op:'missing',value:''}})).toBe(true);
});

it('leaves the document visible when selection is absent, missing, or names a different block type',()=>{
 const page:PageDocument={kind:'headroom.page',version:1,title:'Goal',summary:'',blocks:[{type:'text',id:'note',text:'Keep me'},{type:'checklist',id:'empty',items:[]}]};
 for(const selector of [null,undefined,'missing','note']){
  if(selector!==undefined){
   expect(selectedChecklist(page,selector)).toBeNull();
   expect(pageWithoutSelectedChecklistForRender(page,selector)).toBe(page);
  }
  const goal={kind:'binary',page,primary_checklist_block_id:selector} as GoalResponse;
  expect(resolveGoalPrimaryDisplay(goal)).toEqual({checklist:null,chart:{type:'chart',id:'completion',kind:'binary',placement:'primary'}});
 }
 expect(checklistCounts({type:'checklist',id:'empty',items:[]})).toEqual({checked:0,total:0});
 expect(resolveGoalPrimaryDisplay(null)).toEqual({checklist:null,chart:null});
 expect(resolveGoalPrimaryDisplay(undefined)).toEqual({checklist:null,chart:null});
 expect(resolveGoalPrimaryDisplay({kind:'quantitative',page,primary_chart:null} as GoalResponse)).toEqual({checklist:null,chart:null});
});
