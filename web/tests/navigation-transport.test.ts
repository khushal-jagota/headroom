import {describe,it,expect,vi} from 'vitest';
import {createNavigationTransport} from '../src/lib/navigation/transport';
import {fetchJson} from '../src/lib/api';

describe('navigation transport',()=>{
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
