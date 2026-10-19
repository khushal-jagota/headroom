import {describe,it,expect,vi} from 'vitest';
import {createNavigationTransport,NavigationRequestError} from '../src/lib/navigation/transport';
import {fetchJson} from '../src/lib/api';

describe('navigation transport',()=>{
  it('preserves snapshot read errors without exposing schema or abort failures',async()=>{
    const abort=new AbortController();
    const read=(request:typeof fetch)=>createNavigationTransport({fetch:request}).snapshot(abort.signal);
    await expect(read(async()=>{throw new TypeError('offline');})).rejects.toMatchObject({code:'network',message:'network error'});
    await expect(read(async()=>new Response('unavailable',{status:503}))).rejects.toMatchObject({code:'http_error',message:'HTTP 503',status:503});
    await expect(read(async()=>new Response(JSON.stringify({error:{code:'forbidden',message:'Not permitted',detail:{scope:'owner'}}}),{status:403})))
      .rejects.toMatchObject({code:'forbidden',message:'Not permitted',detail:{scope:'owner'},status:403,isPlannerError:true});
    await expect(read(async()=>new Response('bad JSON'))).rejects.toMatchObject({code:'bad_json',message:'invalid JSON in response',status:200});
    await expect(read(async()=>new Response('{}'))).rejects.not.toBeInstanceOf(NavigationRequestError);
    const cancelled=new DOMException('Cancelled','AbortError');abort.abort();
    await expect(read(async()=>{throw cancelled;})).rejects.toBe(cancelled);
  });
  it('closes native reconnect before error and ignores late events',()=>{
    const source=new EventTarget() as EventSource;
    source.close=vi.fn();
    const factory=vi.fn(()=>source);
    const transport=createNavigationTransport({eventSource:factory});
    const batch=vi.fn(),error=vi.fn(),reset=vi.fn();
    transport.connect('published-cursor',{batch,error,reset},new AbortController().signal);
    expect(factory).toHaveBeenCalledWith('/api/navigation/events?cursor=published-cursor');
    source.dispatchEvent(new MessageEvent('batch',{data:'{"revision":"1"}',lastEventId:'received-cursor'}));
    expect(batch).toHaveBeenCalledWith({revision:'1'},'received-cursor');
    source.onerror?.call(source, new Event('error'));
    expect(source.close).toHaveBeenCalledOnce();expect(error).toHaveBeenCalledOnce();
    source.dispatchEvent(new MessageEvent('batch',{data:'{}',lastEventId:'late'}));
    expect(batch).toHaveBeenCalledOnce();
  });
  it('invalid JSON resets while abort closes without recovery callbacks',()=>{
    const source=new EventTarget() as EventSource;source.close=vi.fn();
    const transport=createNavigationTransport({eventSource:()=>source});
    const batch=vi.fn(),error=vi.fn(),reset=vi.fn();const abort=new AbortController();
    transport.connect('cursor',{batch,error,reset},abort.signal);
    abort.abort();
    source.dispatchEvent(new MessageEvent('batch',{data:'bad'}));
    expect(source.close).toHaveBeenCalledOnce();expect(reset).not.toHaveBeenCalled();expect(error).not.toHaveBeenCalled();
  });
});
describe('optional response header access',()=>{
  it('keeps domain result unchanged and supplies an isolated header copy',async()=>{
    const prior=globalThis.fetch;
    globalThis.fetch=vi.fn(async()=>new Response('{"ok":true}',{headers:{'X-Navigation-Fence':'issued'}}));
    try {
      const metadata:{headers?:Headers}={};
      expect(await fetchJson('/command',{responseMetadata:metadata})).toEqual({ok:true});
      expect(metadata.headers?.get('X-Navigation-Fence')).toBe('issued');
      expect(await fetchJson('/command')).toEqual({ok:true});
    } finally {globalThis.fetch=prior;}
  });
  it('retains planner error status, code and detail',async()=>{
    const prior=globalThis.fetch;
    globalThis.fetch=vi.fn(async()=>new Response('{"error":{"code":"rejected","message":"Rejected","detail":{"x":1}}}',{status:409}));
    try {await expect(fetchJson('/command',{responseMetadata:{}})).rejects.toMatchObject({name:'PlannerFetchError',code:'rejected',status:409,detail:{x:1},isPlannerError:true});}
    finally {globalThis.fetch=prior;}
  });
});
