# ruff: noqa: E501
"""Bounded setup proof for native scrollback."""

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
  let ended=false,endedPosition=null,last=null,stable=0;
  const input=event=>{
    ended=false;endedPosition=null;stable=0;last=null;
    const record={type:event.type,trusted:event.isTrusted,key:event.key,deltaY:event.deltaY,
      target:{tag:event.target?.tagName,thread:!!event.target?.closest?.('[data-conversation-thread]')},
      at:performance.now(),defaultPrevented:event.defaultPrevented};
    queueMicrotask(()=>{record.defaultPrevented=event.defaultPrevented});
    if(events.length<20)events.push(record);
  };
  const position=()=>[e.scrollTop,e.scrollLeft];
  const completed=()=>ended&&JSON.stringify(position())===JSON.stringify(endedPosition);
  const scroll=()=>{
    // Chromium can queue a final scroll event after scrollend at the same position.
    // Only another displacement invalidates that completed native scroll.
    if(ended&&!completed()){ended=false;endedPosition=null;}
    if(events.length<20)events.push({type:'scroll',at:performance.now(),scrollTop:e.scrollTop});
  };
  const end=event=>{
    if(event.target!==e||!event.isTrusted)return;
    ended=true;endedPosition=position();
    if(events.length<20)events.push({type:'scrollend',trusted:event.isTrusted,at:performance.now(),scrollTop:e.scrollTop});
  };
  e.addEventListener(args.kind==='wheel'?'wheel':'keydown',input,true);
  e.addEventListener('scroll',scroll);
  e.addEventListener('scrollend',end);
  window[args.token]={
    sample(){const after=read(e),same=e===document.querySelector('[data-conversation-thread]');
      const native=events.some(event=>event.trusted&&event.type===args.kind&&(!args.key||event.key===args.key));
      const valid=same&&ready(after)&&native&&completed()&&after.scrollTop<before.scrollTop-1&&after.gap>=args.clearance;
      const geometry=JSON.stringify([after.scrollTop,after.scrollHeight,after.clientHeight,after.top,after.left,after.width,after.height]);
      stable=valid&&geometry===last?stable+1:0;last=geometry;
      return valid&&stable>=2;},
    evidence(){const after=read(e);
      const native=events.some(event=>event.trusted&&event.type===args.kind&&(!args.key||event.key===args.key));
      const failure=!native?'no native input delivered':!ready(after)?'transcript no longer ready':
        e!==document.querySelector('[data-conversation-thread]')?'transcript replaced':
        after.scrollTop>=before.scrollTop-1?'no upward displacement':!completed()?'no scrollend':
        after.gap<args.clearance?'insufficient bottom clearance':stable<2?'geometry not stable':null;
      return {failure,before,after,events,ended:completed(),endedPosition,stableIntervals:stable,
      sameTranscript:e===document.querySelector('[data-conversation-thread]'),requiredClearance:args.clearance};},
    cleanup(){e.removeEventListener(args.kind==='wheel'?'wheel':'keydown',input,true);e.removeEventListener('scroll',scroll);e.removeEventListener('scrollend',end);}
  };
}"""
