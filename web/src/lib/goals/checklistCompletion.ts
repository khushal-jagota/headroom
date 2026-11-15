import type {Block,ChecklistBlock,PageDocument} from './contracts';

function checklistsInBlocks(blocks:Block[]):ChecklistBlock[]{
 return blocks.flatMap(block=>{
  if(block.type==='checklist')return [block];
  if(block.type==='grid')return checklistsInBlocks(block.blocks);
  if(block.type==='section')return [...checklistsInBlocks(block.blocks??[]),...(block.views??[]).flatMap(view=>checklistsInBlocks(view.blocks))];
  if(block.type==='segmented')return block.views.flatMap(view=>checklistsInBlocks(view.blocks));
  return [];
 });
}

/** Enumerate saved checklists in document order, including every nested view. */
export function savedChecklists(page:PageDocument):ChecklistBlock[]{
 return checklistsInBlocks(page.blocks);
}

export function selectedChecklist(page:PageDocument,selector:string|null):ChecklistBlock|null{
 return selector===null?null:savedChecklists(page).find(block=>block.id===selector)??null;
}

export function checklistCounts(checklist:ChecklistBlock):{checked:number;total:number}{
 return {checked:checklist.items.filter(item=>item.state==='done').length,total:checklist.items.length};
}

function excludeChecklist(blocks:Block[],id:string):Block[]{
 return blocks.filter(block=>block.type!=='checklist'||block.id!==id).map(block=>{
  if(block.type==='grid')return {...block,blocks:excludeChecklist(block.blocks,id)};
  if(block.type==='section')return {...block,...(block.blocks?{blocks:excludeChecklist(block.blocks,id)}:{}),...(block.views?{views:block.views.map(view=>({...view,blocks:excludeChecklist(view.blocks,id)}))}:{})};
  if(block.type==='segmented')return {...block,views:block.views.map(view=>({...view,blocks:excludeChecklist(view.blocks,id)}))};
  return block;
 });
}

/** Render projection only. Persist edits against the original saved page. */
export function pageWithoutSelectedChecklistForRender(page:PageDocument,selector:string|null):PageDocument{
 const checklist=selectedChecklist(page,selector);
 return checklist?{...page,blocks:excludeChecklist(page.blocks,checklist.id)}:page;
}
