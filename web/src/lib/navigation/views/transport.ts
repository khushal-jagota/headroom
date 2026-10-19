import type {Fence,ViewFrame,ViewRequest,ViewSnapshot} from '../contracts';
import {parseFrame} from './validation';

const MAX_FRAME_BYTES=800*1024;
function path(kind:'snapshot'|'events',view:ViewRequest,fence?:Fence) {
  const query=new URLSearchParams({view:JSON.stringify(view)});
  if(fence) query.set('fence',JSON.stringify(fence));
  return `/api/navigation/view/${kind}?${query}`;
}
export function createViewTransport() {
  return {
    async snapshot(view:ViewRequest,signal:AbortSignal,fence?:Fence):Promise<ViewSnapshot> {
      const response=await fetch(path('snapshot',view,fence),{signal,cache:'no-store'});
      if(!response.ok) throw new Error(`Navigation view unavailable (${response.status})`);
      const body=await response.text();if(new TextEncoder().encode(body).byteLength>MAX_FRAME_BYTES)throw new Error('Navigation frame overflow');
      const result=parseFrame(JSON.parse(body));if(result.event!=='snapshot')throw new Error('Expected view snapshot');return result;
    },
    connect(view:ViewRequest,callbacks:{frame(value:ViewFrame):void;error():void},signal:AbortSignal):()=>void {
      const source=new EventSource(path('events',view));
      const frame=(event:MessageEvent<string>)=>{try{if(new TextEncoder().encode(event.data).byteLength>MAX_FRAME_BYTES)throw new Error('Navigation frame overflow');callbacks.frame(parseFrame(JSON.parse(event.data)));}catch{source.close();callbacks.error();}};
      for(const event of ['snapshot','patch','checkpoint','reset'])source.addEventListener(event,frame as EventListener);
      source.onerror=()=>{source.close();callbacks.error();};
      const close=()=>source.close();signal.addEventListener('abort',close,{once:true});
      return ()=>{signal.removeEventListener('abort',close);close();};
    },
  };
}
export type ViewTransport=ReturnType<typeof createViewTransport>;
