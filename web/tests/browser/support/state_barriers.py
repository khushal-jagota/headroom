# ruff: noqa: E501
"""Bounded setup proofs for native scrollback and navigation projection."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, cast

from playwright.sync_api import Page

_DEADLINE_SECONDS = 5


def establish_scrollback(
    page: Page, action: Callable[[], object], *, min_clearance: float = 2,
    input_kind: str = "wheel", key: str | None = None,
) -> dict[str, Any]:
    """Execute one native action against one ready transcript, then prove its result."""
    deadline = time.monotonic() + _DEADLINE_SECONDS
    token = "__scrollback_" + uuid.uuid4().hex
    args = {"token": token, "clearance": min_clearance, "kind": input_kind, "key": key}
    def remaining() -> float:
        return max(1, (deadline - time.monotonic()) * 1000)
    try:
        page.wait_for_function(_SCROLL_READY, timeout=remaining())
        page.evaluate(_SCROLL_ARM, args)
        action()
        page.wait_for_function("token=>window[token].sample()", arg=token, timeout=remaining())
        return cast(dict[str, Any], page.evaluate("token=>window[token].evidence()", token))
    except Exception as error:
        evidence = page.evaluate("token=>{" + _SCROLL_READ + "return window[token]?.evidence()??{failure:'transcript never became ready',after:read(document.querySelector('[data-conversation-thread]'))}}", token)
        raise AssertionError({"barrier": "scrollback", "evidence": evidence}) from error
    finally:
        page.evaluate("token=>{window[token]?.cleanup();delete window[token]}", token)


_SCROLL_READ = """const read=e=>{
  const region=e?.closest('.conversation-transcript-region'),r=e?.getBoundingClientRect();
  const visible=!!e&&e.isConnected&&r.width>0&&r.height>0&&
    [e,region].every(n=>n&&getComputedStyle(n).visibility==='visible'&&getComputedStyle(n).display!=='none');
  return {connected:!!e?.isConnected,visible,ready:e?.dataset.conversationPlacementReady,
    entry:region?.dataset.conversationEntry,busy:region?.getAttribute('aria-busy'),
    owner:e?.dataset.conversationPositionOwner,motion:!!e?.closest('[data-conversation-pane]')?.hasAttribute('data-conversation-motion'),
    scrollTop:e?.scrollTop,scrollHeight:e?.scrollHeight,clientHeight:e?.clientHeight,
    top:r?.top,left:r?.left,width:r?.width,height:r?.height,
    gap:e?e.scrollHeight-e.clientHeight-e.scrollTop:null};
};
const ready=s=>s.connected&&s.visible&&s.ready==='true'&&s.busy==='false'&&
  !['loading','placing','error'].includes(s.entry)&&!s.motion;
"""
_SCROLL_READY = "()=>{" + _SCROLL_READ + "return ready(read(document.querySelector('[data-conversation-thread]')))}"
_SCROLL_ARM = """args=>{""" + _SCROLL_READ + """
  const e=document.querySelector('[data-conversation-thread]'),before=read(e),events=[];
  if(!ready(before))throw Error('Transcript readiness changed before action');
  let ended=false,last=null,stable=0;
  const input=event=>{
    ended=false;stable=0;last=null;
    const record={type:event.type,trusted:event.isTrusted,key:event.key,deltaY:event.deltaY,
      target:{tag:event.target?.tagName,thread:!!event.target?.closest?.('[data-conversation-thread]')},
      at:performance.now(),defaultPrevented:event.defaultPrevented};
    queueMicrotask(()=>{record.defaultPrevented=event.defaultPrevented});
    if(events.length<20)events.push(record);
  };
  const scroll=()=>{ended=false;if(events.length<20)events.push({type:'scroll',at:performance.now(),scrollTop:e.scrollTop});};
  const end=()=>{ended=true;if(events.length<20)events.push({type:'scrollend',at:performance.now(),scrollTop:e.scrollTop});};
  e.addEventListener(args.kind==='wheel'?'wheel':'keydown',input,true);
  e.addEventListener('scroll',scroll);
  e.addEventListener('scrollend',end);
  window[args.token]={
    sample(){const after=read(e),same=e===document.querySelector('[data-conversation-thread]');
      const native=events.some(event=>event.trusted&&event.type===args.kind&&(!args.key||event.key===args.key));
      const valid=same&&ready(after)&&native&&ended&&after.scrollTop<before.scrollTop-1&&after.gap>=args.clearance;
      const geometry=JSON.stringify([after.scrollTop,after.scrollHeight,after.clientHeight,after.top,after.left,after.width,after.height]);
      stable=valid&&geometry===last?stable+1:0;last=geometry;
      return valid&&stable>=2;},
    evidence(){const after=read(e);
      const native=events.some(event=>event.trusted&&event.type===args.kind&&(!args.key||event.key===args.key));
      const failure=!native?'no native input delivered':!ready(after)?'transcript no longer ready':
        e!==document.querySelector('[data-conversation-thread]')?'transcript replaced':
        after.scrollTop>=before.scrollTop-1?'no upward displacement':!ended?'no scrollend':
        after.gap<args.clearance?'insufficient bottom clearance':stable<2?'geometry not stable':null;
      return {failure,before,after,events,ended,stableIntervals:stable,
      sameTranscript:e===document.querySelector('[data-conversation-thread]'),requiredClearance:args.clearance};},
    cleanup(){e.removeEventListener(args.kind==='wheel'?'wheel':'keydown',input,true);e.removeEventListener('scroll',scroll);e.removeEventListener('scrollend',end);}
  };
}"""


def refresh_projection(
    page: Page, *, required_content: list[dict[str, str]] | None = None,
    required_recaps: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Admit only the exact active refresh receipt and its rendered membership."""
    deadline = time.monotonic() + _DEADLINE_SECONDS
    token = "__projection_" + uuid.uuid4().hex
    try:
        page.evaluate(_PROJECTION_START, {"token": token, "required": required_content or [], "recaps": required_recaps or [], "milliseconds": _DEADLINE_SECONDS * 1000})
        page.wait_for_function("token=>{const s=window[token];if(s.error)throw Error(s.error);return s.sample()}",
                               arg=token, timeout=max(1, (deadline-time.monotonic())*1000))
        return cast(dict[str, Any], page.evaluate("token=>window[token].evidence()", token))
    except Exception as error:
        evidence = page.evaluate("token=>window[token]?.evidence()", token)
        raise AssertionError({"barrier": "projection", "evidence": evidence}) from error
    finally:
        page.evaluate("token=>{window[token]?.cleanup();delete window[token]}", token)


_PROJECTION_START = """({token,required,recaps,milliseconds})=>{
  const controller=new AbortController(),initial=window.__navigationSources();
  const state={receipts:null,error:null,checks:[],
    sample(){
      if(!this.receipts)return false;
      const active=window.__navigationSources();
      if(active.length!==initial.length||initial.some(source=>!active.includes(source)||source.closed)){
        this.error='Navigation subscription closed or replaced';return false;}
      const checks=[],records=[];
      for(const source of initial){
        const receipt=this.receipts.find(item=>item.sourceId===source.id);
        if(!receipt||!receipt.dispatched||receipt.refreshId!==source.generation||receipt.url!==source.url){
          this.error='Missing or superseded active dispatch receipt';return false;}
        const snapshot=receipt.snapshot,request=receipt.request;
        if(snapshot.scope_generation!==request.scope_generation){this.error='Scope generation differs';return false;}
        records.push(...snapshot.content.records);
        const windows=snapshot.content.windows;
        if(windows.length!==request.windows.length||request.windows.some(w=>!windows.some(v=>JSON.stringify(v.request)===JSON.stringify(w)))){
          this.error='Snapshot does not match the exact window request';return false;}
        for(const view of windows){
          const w=view.request;let nodes=[];
          if(w.section==='rail_parents')nodes=[...document.querySelectorAll('.active-work-scroll > .manager-card-slot > .manager-card[data-manager]')];
          else if(w.section==='rail_tickets'){
            const root=w.owner?.kind==='chief'?document.querySelector('.manager-card.chief'):
              [...document.querySelectorAll('.manager-card[data-manager]')].find(n=>n.dataset.manager===w.owner?.id);
            nodes=[...(root?.querySelectorAll('[data-ticket-id]')??[])];
          }else if(w.section==='settled')nodes=[...document.querySelectorAll('[data-settled-item]')];
          else continue;
          const actual=nodes.map(n=>n.dataset.ticketId??n.dataset.manager??n.dataset.settledItem).sort();
          const expected=view.members.map(member=>member.id).sort();
          checks.push({request:w,actual,expected,match:JSON.stringify(actual)===JSON.stringify(expected)});
        }
      }
      const content=required.map(item=>{const nodes=[...document.querySelectorAll(item.selector)];return {...item,
        actual:nodes.map(n=>n.textContent),match:item.text===''?nodes.length===0:nodes.length===1&&nodes[0].textContent===item.text};});
      const recapChecks=recaps.map(item=>{const record=records.find(r=>r.kind==='ticket'&&r.id===item.id);
        return {...item,actual:record?.value.recap,match:record?.value.recap===item.text};});
      this.checks={membership:checks,content,recaps:recapChecks};
      const valid=checks.length>0&&checks.every(c=>c.match)&&content.every(c=>c.match)&&recapChecks.every(c=>c.match);
      return valid;
    },
    evidence(){const bounded=value=>JSON.parse(JSON.stringify(value,(key,item)=>{
      if(typeof item==='string'&&item.length>300)return item.slice(0,300)+'…';
      if(Array.isArray(item)&&item.length>60)return [...item.slice(0,60),{omitted:item.length-60}];
      return item;
    }));return {error:this.error,receipts:bounded(this.receipts?.map(r=>({sourceId:r.sourceId,refreshId:r.refreshId,
      url:r.url,request:r.request,dispatched:r.dispatched,snapshot:{view_id:r.snapshot.view_id,
      scope_generation:r.snapshot.scope_generation,content_version:r.snapshot.content_version,
      windows:r.snapshot.content.windows}}))??null),checks:bounded(this.checks),
      active:window.__navigationSources().map(s=>({id:s.id,url:s.url,generation:s.generation,closed:s.closed}))};},
    cleanup(){clearTimeout(timer);controller.abort();}
  };
  const timer=setTimeout(()=>{state.error='Projection deadline expired';controller.abort()},milliseconds);
  window[token]=state;
  Promise.resolve().then(()=>window.__refreshNavigation({signal:controller.signal})).then(receipts=>{
    if(!controller.signal.aborted)state.receipts=receipts;
  },error=>{if(!controller.signal.aborted)state.error=String(error)});
}"""
