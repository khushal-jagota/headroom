import {afterEach,describe,expect,it,vi} from 'vitest';
import {createScrollWindowCoordinator,type ScrollWindowRestoration} from '../src/lib/navigation/views/scrollWindowRestoration';
import {navigationScrollWindow,type ScrollWindowOptions} from '../src/lib/navigation/views/scrollWindow';
import {viewWindow} from '../src/lib/navigation/views/owner';
import type {ViewWindow} from '../src/lib/navigation/contracts';
import {createRailMotionController,type WorkspaceRailPresentation} from '../src/lib/workspaceRailMotion';
import {buildWorkspaceRail} from '../src/lib/workspaceRail';

afterEach(()=>vi.unstubAllGlobals());

describe('one restoration phase per physical scroller',()=>{
  it('gives priority only to eligible parents and explicitly defers the losing card',()=>{
    const coordinator=createScrollWindowCoordinator(),surface={} as HTMLElement,calls:string[]=[];
    const entry=(name:string,priority:'parent'|'card',ready:boolean):ScrollWindowRestoration & {ready:boolean}=>({
      surface,priority,ready,dispose(){calls.push('dispose '+name)},eligible(){return this.ready},
      restore(){calls.push('restore '+name);this.ready=false},synchronize(){calls.push('sync '+name)},
    });
    const card=entry('card','card',true),parent=entry('parent','parent',false);
    coordinator.register(card);coordinator.register(parent);
    expect(coordinator.flush()).toBe(false);
    expect(calls.filter(v=>v.startsWith('restore'))).toEqual(['restore card']);
    expect(calls.filter(v=>v.startsWith('sync'))).toEqual(['sync card','sync parent']);
    card.ready=true;parent.ready=true;calls.length=0;
    expect(coordinator.flush()).toBe(true);
    expect(calls.filter(v=>v.startsWith('restore'))).toEqual(['restore parent']);
    expect(card.ready).toBe(true);expect(coordinator.flush()).toBe(false);
    expect(calls.filter(v=>v.startsWith('restore'))).toEqual(['restore parent','restore card']);
    coordinator.dispose();
  });
  it('automatically starts the losing card phase even when both pages share one publication',async()=>{
    const coordinator=createScrollWindowCoordinator(),surface={} as HTMLElement,calls:string[]=[];
    for(const priority of ['card','parent'] as const){let ready=true;coordinator.register({surface,priority,
      eligible:()=>ready,restore(){calls.push(priority);ready=false},synchronize(){},dispose(){ready=false}})}
    let state:WorkspaceRailPresentation={rail:buildWorkspaceRail([]),settledOpen:false,scrollPublication:{publicationId:7,windows:[]}};
    const applied:number[]=[];
    const motion=createRailMotionController(()=>null,()=>state,next=>{state=next;applied.push(next.scrollPublication!.publicationId!)},()=>coordinator.flush());
    try {
      motion.update(state);for(let i=0;i<30;i++)await Promise.resolve();
      expect(calls).toEqual(['parent','card']);expect(applied).toEqual([7,7]);
    } finally {motion.dispose();coordinator.dispose()}
  });
  it('retires disappearing registrations and disposal, while independent surfaces proceed',()=>{
    const coordinator=createScrollWindowCoordinator(),restore=vi.fn(),dispose=vi.fn();
    const entry=():ScrollWindowRestoration=>({surface:{} as HTMLElement,priority:'card',dispose,eligible:()=>true,restore,synchronize:()=>{}});
    const remove=coordinator.register(entry());remove();
    coordinator.register(entry());coordinator.register(entry());coordinator.flush();expect(restore).toHaveBeenCalledTimes(2);
    coordinator.dispose();const count=dispose.mock.calls.length;
    coordinator.register(entry());expect(coordinator.flush()).toBe(false);
    expect(restore).toHaveBeenCalledTimes(2);expect(dispose).toHaveBeenCalledTimes(count);
  });
});

/** Action/event admission is synthetic; actual DOM/layout is proved in the App. */
function actionFixture() {
  const frames=new Map<number,FrameRequestCallback>();let nextFrame=0;
  vi.stubGlobal('requestAnimationFrame',(cb:FrameRequestCallback)=>{frames.set(++nextFrame,cb);return nextFrame});
  vi.stubGlobal('cancelAnimationFrame',(id:number)=>frames.delete(id));
  vi.stubGlobal('MutationObserver',class {observe(){}disconnect(){}});
  class Surface extends EventTarget {
    parentElement:null=null;isConnected=true;clientHeight=100;scrollHeight=100;writes:number[]=[];value=0;
    get scrollTop(){return this.value}set scrollTop(value:number){this.writes.push(value);this.value=value}
    getBoundingClientRect(){return {top:0,bottom:100}}
  }
  const surface=new Surface();let rows=[{id:'a',top:10},{id:'b',top:40}];
  class Root extends EventTarget {
    parentElement=surface;isConnected=true;closest(){return null}
    getBoundingClientRect(){return {top:0,bottom:100}}
    querySelectorAll(){return rows.map(row=>({id:row.id,getBoundingClientRect:()=>({top:row.top-surface.scrollTop,bottom:row.top+20-surface.scrollTop})}))}
  }
  const root=new Root();
  vi.stubGlobal('getComputedStyle',(node:unknown)=>({overflowY:node===surface?'auto':'visible'}));
  const coordinator=createScrollWindowCoordinator(),move=vi.fn();
  const page=(cursor:string|null=null,direction:'after'|'before'='after',ids=['a','b']):ViewWindow=>({
    window_id:'stable-window',request:viewWindow('rail_tickets',{kind:'manager',id:'owner'},{anchor:cursor,direction}),
    members:ids.map(id=>({kind:'ticket' as const,id})),total:3,previous:'prev',next:'next',forward_overlap:'next',backward_overlap:'prev',
  });
  const options=(publicationId=1,window=page(),members=window.members.map(key=>key.id)):ScrollWindowOptions=>({
    window,selector:'row',key:node=>(node as unknown as {id:string}).id,move,
    pageRestoration:{coordinator,priority:'card',publicationId,members},
  });
  const action=navigationScrollWindow(root as unknown as HTMLElement,options());
  const press=(key='End')=>{const e=new Event('keydown',{cancelable:true});Object.defineProperty(e,'key',{value:key});root.dispatchEvent(e);return e};
  const frame=()=>{const batch=[...frames.values()];frames.clear();batch.forEach(cb=>cb(0))};
  return {root,newRoot:()=>new Root(),surface,coordinator,action,move,page,options,press,frame,rows:(value:typeof rows)=>{rows=value},dispose:()=>{action.destroy();coordinator.dispose()}};
}

describe('explicit intent waits for the actual rendered page',()=>{
  it('does not restore ordinary publications or interpret another owner restoration as user travel',()=>{
    const f=actionFixture();let sibling:ReturnType<typeof navigationScrollWindow>|undefined;
    try {
      f.action.update(f.options(2));f.coordinator.flush();f.frame();
      expect(f.surface.writes).toEqual([]);expect(f.move).not.toHaveBeenCalled();
      const root=f.newRoot(),siblingMove=vi.fn();
      root.querySelectorAll=()=>[{id:'x',getBoundingClientRect:()=>({top:-f.surface.scrollTop,bottom:20-f.surface.scrollTop})},
        {id:'y',getBoundingClientRect:()=>({top:20-f.surface.scrollTop,bottom:40-f.surface.scrollTop})}];
      sibling=navigationScrollWindow(root as unknown as HTMLElement,{...f.options(2,f.page(null,'after',['x','y'])),move:siblingMove});
      f.press();f.rows([{id:'a',top:110},{id:'c',top:140}]);
      f.action.update(f.options(3,f.page('next','after',['a','c'])));f.coordinator.flush();
      expect(f.surface.writes).toEqual([100]);
      f.surface.dispatchEvent(new Event('scroll'));f.frame();
      expect(siblingMove).not.toHaveBeenCalled();
      f.surface.scrollTop=101;f.surface.dispatchEvent(new Event('scroll'));f.frame();
      expect(siblingMove).toHaveBeenLastCalledWith('next','after');
    } finally {sibling?.destroy();f.dispose()}
  });
  it('requires a newer publication and matching DOM membership',()=>{
    const f=actionFixture();const {action,coordinator,options,page,surface,press,move}=f;
    try {
      expect(press().defaultPrevented).toBe(true);expect(move).toHaveBeenLastCalledWith('next','after');
      f.rows([{id:'a',top:40},{id:'c',top:70}]);
      action.update(options(1,page('next','after',['a','c'])));coordinator.flush();
      action.update(options(2,page()));coordinator.flush();
      expect(surface.writes).toEqual([]);
      action.update(options(2,page('next','after',['a','c']),['a','b']));coordinator.flush();
      expect(surface.writes).toEqual([]);
      action.update(options(2,page('next','after',['a','c'])));expect(coordinator.flush()).toBe(false);
      expect(surface.writes).toEqual([30]);coordinator.flush();f.frame();expect(surface.writes).toEqual([30]);
    } finally {f.dispose()}
  });
  it('retires a superseded cursor or direction and completes a page fetch without a visible anchor',()=>{
    for(const direction of ['after','before'] as const){
      const f=actionFixture();try{
        f.press();f.action.update(f.options(2,f.page(direction==='after'?'superseded':'next',direction)));f.coordinator.flush();
        expect(f.surface.writes).toEqual([]);f.press();expect(f.move).toHaveBeenCalledTimes(2);
      }finally{f.dispose()}
    }
    const f=actionFixture();try{
      f.rows([{id:'a',top:200},{id:'b',top:240}]);f.press();
      f.action.update(f.options(2,f.page('next')));f.coordinator.flush();
      expect(f.surface.writes).toEqual([]);f.press();expect(f.move).toHaveBeenCalledTimes(2);
    }finally{f.dispose()}
  });
  it('retires missing anchors or owners so a later explicit request is not stuck pending',()=>{
    const f=actionFixture();const {action,coordinator,options,page,press,move,surface}=f;
    try {
      press();f.rows([{id:'c',top:40}]);action.update(options(2,page('next','after',['c'])));coordinator.flush();
      expect(surface.writes).toEqual([]);press();expect(move).toHaveBeenCalledTimes(2);
      f.root.isConnected=false;coordinator.flush();f.root.isConnected=true;press();expect(move).toHaveBeenCalledTimes(3);
      action.destroy();coordinator.flush();f.frame();expect(surface.writes).toEqual([]);
    } finally {f.dispose()}
  });
});
