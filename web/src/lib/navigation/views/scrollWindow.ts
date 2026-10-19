import type {ViewWindow} from '../contracts';

type Options={window:ViewWindow|undefined;selector:string;key:(node:HTMLElement)=>string;move:(anchor:string,direction:'before'|'after')=>void;activate?:()=>void};
/** Replace overlapping windows while retaining a real surviving row's screen position. */
export function navigationScrollWindow(root:HTMLElement,initial:Options) {
  let options=initial;
  let scroll:HTMLElement|null=root;
  while(scroll && !/(auto|scroll)/.test(getComputedStyle(scroll).overflowY))scroll=scroll.parentElement;
  const surface=scroll ?? document.scrollingElement as HTMLElement;
  const target:HTMLElement|Window=scroll ?? window;
  let anchor:{key:string;top:number}|undefined;
  let pending:string|undefined;
  let frame=0;
  let disposed=false;
  let travel=0;
  let lastTop=surface.scrollTop;
  let restoring=false;
  const viewportTop=()=>scroll?scroll.getBoundingClientRect().top:0;
  const nodes=()=>[...root.querySelectorAll<HTMLElement>(options.selector)];
  function remember() {
    const top=viewportTop();
    const node=nodes().find(node=>node.getBoundingClientRect().bottom>top);
    if(node)anchor={key:options.key(node),top:node.getBoundingClientRect().top-top};
  }
  function demand() {
    if(disposed||pending)return;
    const rows=nodes(),view=options.window;
    if(!view||!rows.length)return;
    const top=viewportTop(),bottom=scroll?scroll.getBoundingClientRect().bottom:window.innerHeight;
    const middle=rows[Math.floor(rows.length/2)];
    const forward=view.forward_overlap??view.next,backward=view.backward_overlap??view.previous;
    // The overlap retains the visible half. Fetch before its last rows leave the viewport.
    if(travel>0 && forward && middle.getBoundingClientRect().top<=top+1) {
      turn('after',forward);
    } else if(travel<0 && backward && middle.getBoundingClientRect().top>=bottom) {
      turn('before',backward);
    }
  }
  function turn(direction:'before'|'after',cursor:string):boolean {
    if(pending)return false;
    pending=cursor;travel=0;remember();options.activate?.();options.move(cursor,direction);return true;
  }
  // A short page cannot produce native scroll events. Only explicit user intent
  // crosses its boundary; mounting or mutation never walks through archive pages.
  function boundary(direction:'before'|'after'):boolean {
    const view=options.window;if(disposed||pending||!view)return false;
    const bounds=root.getBoundingClientRect(),top=viewportTop();
    const bottom=scroll?scroll.getBoundingClientRect().bottom:window.innerHeight;
    if(bounds.bottom<=top||bounds.top>=bottom)return false;
    if(direction==='after' && surface.scrollTop+surface.clientHeight<surface.scrollHeight-1)return false;
    if(direction==='before' && surface.scrollTop>1)return false;
    if(!view.members.length && view.total>0){options.activate?.();return true;}
    const cursor=direction==='after'?(view.forward_overlap??view.next):(view.backward_overlap??view.previous);
    return cursor?turn(direction,cursor):false;
  }
  function wheel(event:WheelEvent){if(event.deltaY&&boundary(event.deltaY>0?'after':'before')){event.preventDefault();event.stopPropagation();}}
  let touchY:number|undefined;
  function touchStart(event:TouchEvent){touchY=event.touches[0]?.clientY;}
  function touchMove(event:TouchEvent){const next=event.touches[0]?.clientY;if(next===undefined||touchY===undefined)return;const delta=touchY-next;if(Math.abs(delta)>8&&boundary(delta>0?'after':'before')){touchY=next;event.preventDefault();event.stopPropagation();}}
  function key(event:KeyboardEvent){if((event.target as HTMLElement).closest('input,textarea,[contenteditable="true"]'))return;const direction=['ArrowDown','PageDown','End'].includes(event.key)?'after':['ArrowUp','PageUp','Home'].includes(event.key)?'before':undefined;if(direction&&boundary(direction)){event.preventDefault();event.stopPropagation();}}
  function activateEmpty(){if(options.window?.total&&!options.window.members.length)options.activate?.();}
  root.addEventListener('pointerenter',activateEmpty);root.addEventListener('focusin',activateEmpty);
  root.addEventListener('wheel',wheel,{passive:false});root.addEventListener('touchstart',touchStart,{passive:true});root.addEventListener('touchmove',touchMove,{passive:false});root.addEventListener('keydown',key);
  function schedule(restore=false) {
    cancelAnimationFrame(frame);
    frame=requestAnimationFrame(()=>{
      if(disposed)return;
      if(restore&&anchor){const node=nodes().find(node=>options.key(node)===anchor!.key);if(node){restoring=true;surface.scrollTop+=node.getBoundingClientRect().top-viewportTop()-anchor.top;lastTop=surface.scrollTop;queueMicrotask(()=>restoring=false);}}
      remember();demand();
    });
  }
  function onScroll(){if(!restoring)travel=Math.sign(surface.scrollTop-lastTop);lastTop=surface.scrollTop;remember();schedule();}
  target.addEventListener('scroll',onScroll,{passive:true});
  const observer=new MutationObserver(()=>schedule(true));observer.observe(root,{childList:true,subtree:true});
  schedule();
  return {update(next:Options){if(next.window!==options.window)pending=undefined;options=next;schedule(true);},destroy(){disposed=true;cancelAnimationFrame(frame);observer.disconnect();target.removeEventListener('scroll',onScroll);root.removeEventListener('pointerenter',activateEmpty);root.removeEventListener('focusin',activateEmpty);root.removeEventListener('wheel',wheel);root.removeEventListener('touchstart',touchStart);root.removeEventListener('touchmove',touchMove);root.removeEventListener('keydown',key);}};
}
