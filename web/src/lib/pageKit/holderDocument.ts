import type {Block,PageDocument} from '../goals/contracts';
export function hasTickets(document:PageDocument|null|undefined):boolean {
 const contains=(blocks:Block[]):boolean=>blocks.some(block=>block.type==='tickets'||('blocks' in block&&contains(block.blocks??[]))||('views' in block&&block.views?.some(view=>contains(view.blocks))));
 return document?contains(document.blocks):false;
}
