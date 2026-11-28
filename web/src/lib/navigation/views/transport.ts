import { clientFetch } from "../../clientIdentity";
import {subscribeLive} from '../../liveSocket';
import type {ViewFrame,ViewRequest,ViewSnapshot} from '../contracts';

function snapshotPath(view:ViewRequest) {
  const query=new URLSearchParams({view:JSON.stringify(view)});
  return `/api/navigation/view/snapshot?${query}`;
}
export function createViewTransport() {
  return {
    async snapshot(view:ViewRequest,signal:AbortSignal):Promise<ViewSnapshot> {
      const response=await clientFetch(snapshotPath(view),{signal,cache:'no-store'});
      if(!response.ok) throw new Error(`Navigation view unavailable (${response.status})`);
      return await response.json() as ViewSnapshot;
    },
    connect(view:ViewRequest,callbacks:{frame(value:ViewFrame):void;error():void},signal:AbortSignal):()=>void {
      const stop=subscribeLive({kind:'navigation',params:{view:JSON.stringify(view)}},{
        frame(event,data){
          if(!['snapshot','patch','checkpoint','reset'].includes(event))return;
          try{callbacks.frame(data as ViewFrame);}catch{stop();callbacks.error();}
        },
        lost(){callbacks.error();},
      });
      signal.addEventListener('abort',stop,{once:true});
      return ()=>{signal.removeEventListener('abort',stop);stop();};
    },
  };
}
export type ViewTransport=ReturnType<typeof createViewTransport>;
