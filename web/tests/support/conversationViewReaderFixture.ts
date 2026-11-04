/** A finite indexed-page transport for browser lifecycle and scroll admission checks. */
import type {ConversationViewItem,ConversationViewPage,ConversationViewRow,ConversationViewStatus,ViewPageCursor,ViewRowBody} from '../../src/lib/conversation/view/contracts';
import { VIEW_PROJECTION_VERSION } from '../../src/lib/conversation/view/contracts';
export function installConversationViewReaderFixture(id='bounded-reader', options: {search?: string; baseUrl?: string} = {}):void {
  const search = options.search ?? location.search;
  const baseUrl = options.baseUrl ?? location.origin;
  const textRestoration=new URLSearchParams(search).get('text-restoration');
  const restorationText=textRestoration==='tall'
    ? '```text\n'+Array.from({length:1400},(_,index)=>`Interior code line ${index}: preserve this exact character through late layout.`).join('\n')+'\n```'
    : Array.from({length:400},(_,index)=>`Late paragraph 2000.${index}. `+'The final body differs from its provisional skeleton. '.repeat(3)).join('\n\n');
  const openingRequest=new URLSearchParams(search).get('opening-request');
  const positionTakeover=new URLSearchParams(search).has('position-takeover');
  const positionRace=new URLSearchParams(search).has('position-race');
  const positionBodies: Array<()=>void>=[];
  const positionProbe={hold:true,waiting:0,release:()=>{positionProbe.hold=false;for(const release of positionBodies.splice(0))release();}};
  (window as any).__positionProbe=positionProbe;
  const unreadText=new URLSearchParams(search).get('unread');
  const unread=unreadText===null?null:Number(unreadText);
  const ownerWidth=new URLSearchParams(search).get('owner-width');
  const widthBody=JSON.stringify({kind:'prompt',sender_label:'owner',mode:'queue',sent_at_unix_milliseconds:1,content:[{piece:'text',text:'Short prompt.'}]});
  let releaseOwnerBody: (()=>void)|null=null;
  (window as any).__releaseOwnerBody=()=>releaseOwnerBody?.();
  const expiry=new URLSearchParams(search).has('expiry');
  let expired=false;
  let deferredExpired=false;
  const delayedBody=JSON.stringify({kind:'agent_message',to_owner:true,content:[{piece:'text',text:'Late diagnostic body must disappear.'}]});
  let completeBody: (()=>void)|null=null;
  const sources:EventTarget[]=[];
  const folds=new URLSearchParams(search).has('folds');
  const liveStream=new URLSearchParams(search).has('live-stream');
  const visual=new URLSearchParams(search).has('visual');
  const shortLens=new URLSearchParams(search).has('short-lens');
  let sequence=shortLens?1:2000;
  // Return probes reuse this finite transport. Arm only after the initial layout.
  (window as any).__backlog=(count:number)=>{sequence+=count;};
  const returnBodies=new Map<string,string>();
  const returnBodyReleases:Array<()=>void>=[];
  const returnProbe={hold:false,fail:false,inlineTarget:false,bodies:false,bodyHold:false,holdTargetOnly:false,changedSource:false,ignoreAbort:false,missingTarget:false,failLatest:false,bodyWaiting:0,targetBodyWaiting:0,waiting:0,release:()=>{},
    releaseBodies:()=>{returnProbe.bodyHold=false;for(const release of returnBodyReleases.splice(0))release();}};
  (window as any).__returnProbe=returnProbe;
  const calls:string[]=[];
  (window as any).__viewCalls=calls;
  const status:ConversationViewStatus={diagnostics_expired:false,runtime_epoch:'fixture',revision:0,activity_at_unix_milliseconds:null,authority:'current',turn_key:null,turn_running:false,stopped_without_ending:false,pending_permission:null,pending_user_input:null,plan:null,live_output:null,live_tool_key:null,live_tool_progress:null,rest:{latest_happened:{kind:'agent_message',sender_label:null,excerpt:'Latest owner answer'},latest_turn:null,latest_tool:null,permission_title_excerpt:null,latest_owner_message_sequence:2000,latest_owner_message_preview:"Latest owner answer",last_boundary_failed:false,plan_progress:{total_count:0,completed_count:0,current_position:null,current_text_excerpt:null,has_unfinished_entry:false}}};
  let liveText='Temporary streaming prose.';
  let liveVersion=1;
  const deferredEndingText=JSON.stringify({kind:'turn_ended',ending:'completed',error_summary:null,automatic_compaction_result:null});
  if(visual||liveStream)status.live_output={runtime_epoch:'fixture',turn_key:'turn:1',version:1,byte_count:liveText.length,reference:{body_id:'live:fixture',version:'1',byte_count:liveText.length,media_type:'text/markdown'}};
  const previewBodies = new Map<number,ViewRowBody>();
  function item(n:number):ConversationViewRow{return{kind:'row',key:'e'+n,row_kind:'agent_message',order:{sequence:n,slot:0},source_sequence:n,sender_message_id:null,changed_through_sequence:n,created_at:n,turn_key:null,work_group_key:null,fold_rule:'never',body:{kind:'inline',value:{kind:'agent_message',to_owner:true,content:[{piece:'text',text:shortLens ? "Child direct message" : `Owner answer ${n}. `+'The same message remains reachable without all previous bodies in memory. '.repeat(4)}]}}};}
  function visualPage(url:URL):ConversationViewPage{
    const lens=url.searchParams.get('lens')==='full'?'full':'focus';
    const scope=JSON.parse(url.searchParams.get('scope')??'{"kind":"timeline","key":null}');
    const row=(n:number,body:ViewRowBody):ConversationViewRow=>({...item(n),row_kind:body.kind,body:{kind:'inline',value:body}});
    const prose=(n:number,text:string)=>row(n,{kind:'agent_message',to_owner:true,content:[{piece:'text',text}]});
    const tool=(n:number):ConversationViewRow=>({...row(n,{kind:'tool_call',tool_call_id:'tool-'+n,title:'Read file '+n,tool_kind:'read',detail:'File contents '+n,started_detail:'File contents '+n,capped_detail_sequence:null,status:'completed'}),order:{sequence:n,slot:1},work_group_key:'work:5',fold_rule:'work'});
    let items:ConversationViewItem[]=[row(1,{kind:'prompt',sender_label:'owner',mode:'queue',sent_at_unix_milliseconds:1,content:[{piece:'text',text:'Check the conversation.'}]}),prose(2,'First separate answer.'),prose(4,'Second separate answer.'),prose(11,'Final separate answer.')];
    if(lens==='full'){
      items.push(row(0,{kind:'token_usage',input_tokens:1,output_tokens:1,cached_input_tokens:0,cost_usd:null}),row(12,{kind:'token_usage',input_tokens:1,output_tokens:1,cached_input_tokens:0,cost_usd:null}),row(13,{kind:'turn_ended',ending:'completed',error_summary:null,automatic_compaction_result:null}));
      items.push(row(3,{kind:'token_usage',input_tokens:12,output_tokens:8,cached_input_tokens:0,cost_usd:null}),{kind:'work_group',key:'work:5',order:{sequence:5,slot:0},changed_through_sequence:6,turn_key:null,entry_count:2,latest_entry_key:'e6',settled:false},tool(6),row(7,{kind:'turn_ended',ending:'completed',error_summary:null,automatic_compaction_result:null}),row(8,{kind:'turn_ended',ending:'failed',error_summary:'Visible failure',automatic_compaction_result:null}),row(9,{kind:'turn_ended',ending:'interrupted',error_summary:null,automatic_compaction_result:null}),row(10,{kind:'turn_ended',ending:'completed',error_summary:null,automatic_compaction_result:'not_compacted'}));
    }
    if(scope.kind==='work_group')items=[tool(5),tool(6)];
    const ending=items.find((item):item is ConversationViewRow=>item.kind==='row'&&item.key==='e13');
    if(ending)ending.body={kind:'deferred',reference:{body_id:'completed:13',version:'1',byte_count:deferredEndingText.length,media_type:'application/json'}};
    items.sort((a,b)=>a.order.sequence-b.order.sequence||a.order.slot-b.order.slot);
    return{conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,scope,items,older:null,newer:null,body_bytes:new TextEncoder().encode(JSON.stringify(items)).byteLength};
  }
  // A settled owner turn exercises the real reader's Full autoexpansion and the
  // never-fold metadata before a deferred owner body has been materialized.
  const deferredOwner=JSON.stringify({kind:'agent_message',to_owner:true,content:[{piece:'text',text:'Owner answer one.'}]});
  function foldPage(url:URL):ConversationViewPage {
    const lens=url.searchParams.get('lens')==='full'?'full':'focus';
    const scope=JSON.parse(url.searchParams.get('scope')??'{"kind":"timeline","key":null}');
    const row=(n:number,body:ViewRowBody,turn='turn:1'):ConversationViewRow=>({...item(n),row_kind:body.kind,turn_key:turn,body:{kind:'inline',value:body}});
    const prompt=(n:number)=>row(n,{kind:'prompt',sender_label:'owner',mode:'queue',sent_at_unix_milliseconds:n,content:[{piece:'text',text:'Owner prompt '+n}]},'turn:'+n);
    const prose=(n:number,text:string,owner:boolean,turn='turn:1'):ConversationViewRow=>({...row(n,{kind:'agent_message',to_owner:owner,content:[{piece:'text',text}]},turn),fold_rule:owner?'never':'earlier_message'});
    const turn=(n:number,count:number,ending:'completed'|'failed'|null,stopped=false):ConversationViewItem=>({kind:'turn',key:'turn:'+n,order:{sequence:n,slot:1},changed_through_sequence:16,settled:true,stopped,started_at_unix_milliseconds:n,ending,is_latest:n===14,duration_seconds:ending===null?null:1,tool_call_count:lens==='full'&&n===1?2:0,folded_message_count:lens==='full'?count:0,latest_message_key:'e'+(n===1?9:n===11?12:15)});
    const tool=(n:number):ConversationViewRow=>({...row(n,{kind:'tool_call',tool_call_id:'fold-tool-'+n,title:'Stored tool '+n,tool_kind:'read',detail:'Stored detail',started_detail:'Stored detail',capped_detail_sequence:null,status:'completed'}),work_group_key:'work:6',order:{sequence:n,slot:1},fold_rule:'work'});
    const owner=prose(3,'Owner answer one.',true);
    owner.body={kind:'deferred',reference:{body_id:'owner:3',version:'1',byte_count:deferredOwner.length,media_type:'application/json'}};
    let items:ConversationViewItem[]=[prompt(1),turn(1,3,'completed'),owner,prose(5,'Owner answer two.',true),prose(9,'Owner answer three.',true),prompt(11),turn(11,0,'failed'),prose(12,'Owner answer before failure.',true,'turn:11'),row(13,{kind:'turn_ended',ending:'failed',error_summary:'Visible fold failure',automatic_compaction_result:null},'turn:11'),prompt(14),turn(14,0,null,true)];
    if(lens==='full')items.push(prose(15,'Latest ordinary stopped output.',false,'turn:14'));
    if(scope.kind==='turn')items=scope.key==='turn:1'?[prose(2,'Ordinary commentary one.',false),prose(4,'Ordinary commentary two.',false),prose(8,'Ordinary commentary three.',false),{kind:'work_group',key:'work:6',order:{sequence:6,slot:0},changed_through_sequence:10,turn_key:'turn:1',entry_count:2,latest_entry_key:'e7',settled:true}]:[];
    if(scope.kind==='work_group')items=[tool(6),tool(7)];
    items.sort((a,b)=>a.order.sequence-b.order.sequence||a.order.slot-b.order.slot);
    return{conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,scope,items,older:null,newer:null,body_bytes:new TextEncoder().encode(JSON.stringify(items)).byteLength};
  }
  if(folds){status.turn_key='turn:14';status.stopped_without_ending=true;}
  function page(url:URL):ConversationViewPage{
    const lens=url.searchParams.get('lens')==='full'?'full':'focus';
    if(ownerWidth){
      const items:ConversationViewRow[]=[1,2,3].map(n=>({...item(n),row_kind:'prompt',body:{kind:'inline',value:{kind:'prompt',sender_label:n===3?'Chief':'owner',mode:'queue',sent_at_unix_milliseconds:n,content:[{piece:'text',text:n===2?'A long owner prompt retains its existing width limit. '.repeat(8):'Short prompt.'}]}}}));
      items[0].body={kind:'deferred',reference:{body_id:'owner-width:1',version:'1',byte_count:widthBody.length,media_type:'application/json'}};
      return{conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens:'focus',scope:{kind:'timeline',key:null},items,older:null,newer:null,body_bytes:0};
    }
    if(expiry){
      const tool:ConversationViewRow={...item(1),row_kind:'tool_call',body:{kind:'inline',value:{kind:'tool_call',tool_call_id:'expiry-tool',title:'Read diagnostic',tool_kind:'read',detail:'Capped diagnostic',started_detail:'Capped diagnostic',capped_detail_sequence:2,status:'completed'}}};
      const pending:ConversationViewRow={...item(3),body:{kind:'deferred',reference:{body_id:'row:3',version:'1',byte_count:delayedBody.length,media_type:'application/json'}}};
      return{conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,scope:{kind:'timeline',key:null},items:expired&&!deferredExpired?[tool]:[tool,pending],older:null,newer:null,body_bytes:0};
    }
    if(folds)return foldPage(url);
    if(visual)return visualPage(url);
    const cursor=JSON.parse(url.searchParams.get('cursor')??'null') as ViewPageCursor|null;
    const direction=url.searchParams.get('direction')??'older';
    const pageSize=textRestoration?20:100;
    const end=cursor===null?sequence:direction==='older'?cursor.order.sequence-(cursor.order.slot>0?0:1):Math.min(sequence,cursor.order.sequence+pageSize);
    const start=cursor!==null&&direction==='newer'?cursor.order.sequence+1:Math.max(1,end-pageSize+1);
    const items=Array.from({length:end-start+1},(_,index)=>{
      const row=item(start+index);
      const previewBody=previewBodies.get(start+index);
      if(previewBody){row.row_kind=previewBody.kind;row.body={kind:'inline',value:previewBody};}
      if(openingRequest && row.source_sequence===1990){
        row.row_kind=openingRequest==='permission'?'permission_ask':'user_input';
        row.body={kind:'inline',value:openingRequest==='permission'
          ? {kind:'permission_ask',ask_id:'opening-permission',title:'Earlier live permission',detail:null,options:[{option_id:'allow',label:'Allow',option_kind:'allow_once'}],state:'live',dead_reason:null,answered_option_label:null}
          : {kind:'user_input',request_id:'opening-input',questions:[{question_id:'choice',header:'Choice',question:'Earlier live question',options:[{label:'Continue',description:'Continue the work'}],multi_select:false,allow_other:true}],state:'live',dead_reason:null,answers:null,failure_detail:null}};
      }
      if(row.source_sequence>=1997 && row.source_sequence<=1999){
        row.row_kind='prompt';
        row.body={kind:'inline',value:{kind:'prompt',sender_label:row.source_sequence===1997?'Chief':'owner',mode:'queue',sent_at_unix_milliseconds:row.created_at,content:[{piece:'text',text:row.source_sequence===1999?'A long owner prompt retains its existing width limit. '.repeat(20):'Short prompt.'}]}};
      }
      if(textRestoration && row.source_sequence===2000){
        row.row_kind='agent_message';
        const body=JSON.stringify({kind:'agent_message',to_owner:true,content:[{piece:'text',text:restorationText}]});
        returnBodies.set('return:2000',body);
        row.body=returnProbe.inlineTarget
          ? {kind:'inline',value:JSON.parse(body)}
          : {kind:'deferred',reference:{body_id:'return:2000',version:'1',byte_count:new TextEncoder().encode(body).byteLength,media_type:'application/json'}};
      }
      if(positionRace && row.source_sequence>=(positionTakeover?2000:1991)){
        const body=JSON.stringify({kind:'agent_message',to_owner:true,content:[{piece:'text',text:Array.from({length:row.source_sequence===2000?400:18},(_,index)=>`Late paragraph ${row.source_sequence}.${index}. `+'The final body differs from its provisional skeleton. '.repeat(3)).join('\n\n')}]});
        row.row_kind='agent_message';
        row.body={kind:'deferred',reference:{body_id:'position:'+row.source_sequence,version:'1',byte_count:new TextEncoder().encode(body).byteLength,media_type:'application/json'}};
      }
      if(returnProbe.bodies && row.body.kind==='inline' && !(returnProbe.inlineTarget && row.source_sequence===2000)){
        const bodyId='return:'+row.source_sequence;
        const body=JSON.stringify(row.body.value);returnBodies.set(bodyId,body);
        row.body={kind:'deferred',reference:{body_id:bodyId,version:'1',byte_count:new TextEncoder().encode(body).byteLength,media_type:'application/json'}};
      }
      return row;
    });
    if(returnProbe.missingTarget){const index=items.findIndex(row=>row.key==='e2000');if(index>=0)items.splice(index,1);}
    const position=(n:number):ViewPageCursor=>({projection_version:VIEW_PROJECTION_VERSION,lens,scope:{kind:'timeline',key:null},order:{sequence:n,slot:0}});
    return{conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,scope:{kind:'timeline',key:null},items,older:start>1?position(start):null,newer:end<sequence?position(end):null,body_bytes:new TextEncoder().encode(JSON.stringify(items)).byteLength};
  }
  // Gates model a cancellable transport, so superseding history can release the
  // existing serialized reader queue. Forward recovery stays blocked until the
  // destination proof explicitly releases it.
  const latestProbe = {
    holdHistory: false, holdLatest: false, blockForward: false,
    historyWaiting: 0, latestWaiting: 0, forwardWaiting: 0,
    latestResponses: 0,
    releaseHistory: () => {}, releaseLatest: () => {}, releaseForward: () => {},
  };
  (window as any).__latestProbe = latestProbe;
  async function gate(kind: 'history'|'latest'|'forward', signal?: AbortSignal|null): Promise<void> {
    const waiting = `${kind}Waiting` as const;
    latestProbe[waiting] += 1;
    await new Promise<void>((resolve, reject) => {
      const finish = () => {signal?.removeEventListener('abort', abort); resolve();};
      const abort = () => {signal?.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError'));};
      latestProbe[kind === 'history' ? 'releaseHistory' : kind === 'latest' ? 'releaseLatest' : 'releaseForward'] = finish;
      if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, {once:true});
    });
  }
  const originalFetch=window.fetch;
  window.fetch=async(input,init)=>{
    const value=String(input);if(!value.startsWith('/api/conversation/'))return originalFetch(input,init);
    calls.push(value);const url=new URL(value,baseUrl);
    if(url.pathname==='/api/conversation/backends')return Response.json({backends:[]});
    if(url.pathname.endsWith('/view/body/content')){
      const reference=JSON.parse(url.searchParams.get('reference')!);
      if(String(reference.body_id).startsWith('return:')){
        if(returnProbe.bodyHold && (!returnProbe.holdTargetOnly || reference.body_id==='return:2000')){
          returnProbe.bodyWaiting++;if(reference.body_id==='return:2000')returnProbe.targetBodyWaiting++;
          await new Promise<void>(resolve=>returnBodyReleases.push(resolve));
        }
        const body=returnBodies.get(reference.body_id);
        return new Response(returnProbe.changedSource && reference.body_id==='return:2000'
          ? body?.replace('Late paragraph 2000.0.', 'Lost paragraph 2000.0.') : body);
      }
    }
    if(positionRace&&url.pathname.endsWith('/view/body/content')){
      const reference=JSON.parse(url.searchParams.get('reference')!);
      if(String(reference.body_id).startsWith('position:')){
        if(positionProbe.hold){positionProbe.waiting++;await new Promise<void>(resolve=>positionBodies.push(resolve));}
        const sequence=Number(reference.body_id.split(':')[1]);
        return Response.json({kind:'agent_message',to_owner:true,content:[{piece:'text',text:Array.from({length:sequence===2000?400:18},(_,index)=>`Late paragraph ${sequence}.${index}. `+'The final body differs from its provisional skeleton. '.repeat(3)).join('\n\n')}]});
      }
    }
    if(ownerWidth&&url.pathname.endsWith('/view/body/content'))return ownerWidth==='immediate'?new Response(widthBody):await new Promise<Response>(resolve=>{releaseOwnerBody=()=>resolve(new Response(widthBody));});
    if(expiry&&url.pathname.endsWith('/view/body/content')&&deferredExpired)return Response.json({error:{code:'diagnostic_expired',message:'Expired'}},{status:410});
    if(expiry&&url.pathname.endsWith('/view/body/content'))return await new Promise<Response>(resolve=>{completeBody=()=>resolve(new Response(delayedBody));});
    if(expiry&&url.pathname.endsWith('/events/2/detail'))return expired?Response.json({error:{code:'diagnostic_expired',message:'Expired'}},{status:410}):Response.json({detail:'Cached whole diagnostic'});
    if(folds&&url.pathname.endsWith('/view/body/content'))return new Response(deferredOwner);
    if((visual||liveStream)&&url.pathname.endsWith('/view/body/content'))return new Response(JSON.parse(url.searchParams.get('reference')!).body_id==='completed:13'?deferredEndingText:liveText);
    if(url.pathname.endsWith('/view/pages')) {
      if(returnProbe.hold){
        returnProbe.waiting++;
        await new Promise<void>((resolve,reject)=>{
          const abort=()=>reject(new DOMException('Aborted','AbortError'));
          returnProbe.release=()=>{init?.signal?.removeEventListener('abort',abort);resolve();};
          if(!returnProbe.ignoreAbort){
            if(init?.signal?.aborted)abort();else init?.signal?.addEventListener('abort',abort,{once:true});
          }
        });
      }
      if(returnProbe.fail)return Response.json({error:'Return admission failed'},{status:503});
      const cursored=url.searchParams.has('cursor');
      if(!cursored && returnProbe.failLatest)return Response.json({error:'Newest messages could not load'},{status:503});
      if(cursored && url.searchParams.get('direction')==='newer' && latestProbe.blockForward)
        await gate('forward', init?.signal);
      else if(cursored && latestProbe.holdHistory) await gate('history', init?.signal);
      else if(!cursored && latestProbe.holdLatest) await gate('latest', init?.signal);
      if(!cursored) latestProbe.latestResponses += 1;
      return Response.json(page(url));
    }
    if(url.pathname.endsWith('/view/receipts'))return Response.json({conversation_id:id,coverage:{through_sequence:sequence},receipts:[]});
    if(url.pathname.endsWith('/owner-read'))return Response.json({owner_read_through_sequence:JSON.parse(String(init?.body)).through_sequence});
    return Response.json({conversation_id:id,backend_key:'codex',supports_steer:true,model:null,reasoning_effort:null,workspace_folder:'/fixture',access:'full',role_text:null,identity_environment_variable_names:[],latest_sequence:sequence,owner_read_through_sequence:unread??sequence,is_running:false,held_prompts:[],pending_permission_ask:null,pending_user_input:null,composer_catalog:[]});
  };
  class Source extends EventTarget{
    onerror:null|(()=>void)=null;closed=false;
    constructor(readonly raw:string){super();sources.push(this);calls.push(raw);const url=new URL(raw,baseUrl);queueMicrotask(()=>{
      if(this.closed)return;
      const result=page(url);if(url.searchParams.get('transcript')==='false')result.items=[];
      const after=url.searchParams.get('after');
      const value=after===null?{page:result,coverage:{through_sequence:sequence},status}:{kind:'changes',conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens:result.lens,from_sequence:Number(after),coverage:{through_sequence:sequence},upserts:[],scope_changes:[],removed_keys:[],invalidated_scopes:[],status};
      this.dispatchEvent(new MessageEvent(after===null?'snapshot':'changes',{data:JSON.stringify(value)}));
    });}
    close(){this.closed=true;}
  }
  (window as any).__streamProbe={append:(text:string)=>{
    liveText+=text;liveVersion++;
    status.live_output={runtime_epoch:'fixture',turn_key:'turn:1',version:liveVersion,byte_count:liveText.length,reference:{body_id:'live:fixture',version:String(liveVersion),byte_count:liveText.length,media_type:'text/markdown'}};
    const before=sequence;sequence++;status.revision=sequence;
    for(const source of sources){if((source as Source).closed)continue;const lens=new URL((source as Source).raw,baseUrl).searchParams.get('lens');source.dispatchEvent(new MessageEvent('changes',{data:JSON.stringify({kind:'changes',conversation_id:id,protocol_version:1,projection_version:VIEW_PROJECTION_VERSION,lens,from_sequence:before,coverage:{through_sequence:sequence},upserts:[],scope_changes:[],removed_keys:[],invalidated_scopes:[],status})}));}
  }};
  (window as any).__expireDiagnostics=()=>{
    expired=true;status.diagnostics_expired=true;
    for(const source of sources){if((source as Source).closed)continue;source.dispatchEvent(new MessageEvent('snapshot',{data:JSON.stringify({page:page(new URL((source as Source).raw,baseUrl)),coverage:{through_sequence:sequence},status})}));}
  };
  (window as any).__expireDeferred=()=>{deferredExpired=true;(window as any).__expireDiagnostics();};
  (window as any).__completeDiagnosticBody=()=>completeBody?.();
  (window as any).__appendPreviewConversation=(text:string)=>{
    const now=Date.now();
    previewBodies.set(++sequence,{kind:'prompt',sender_label:'owner',mode:'queue',sent_at_unix_milliseconds:now,content:[{piece:'text',text}]});
    previewBodies.set(++sequence,{kind:'agent_message',to_owner:true,content:[{piece:'text',text:'This is a local sample reply. Your message stays in this preview.'}]});
    for(const source of sources){if((source as Source).closed)continue;
      source.dispatchEvent(new MessageEvent('snapshot',{data:JSON.stringify({page:page(new URL((source as Source).raw,baseUrl)),coverage:{through_sequence:sequence},status})}));}
  };
  window.EventSource=Source as unknown as typeof EventSource;
}
