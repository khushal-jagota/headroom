"""The live admission callback cannot escape on another operation's early scrollend."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_native_wheel_operation_owns_baseline() -> None:
    root = Path(__file__).resolve().parents[2]
    probe = r'''
import assert from 'node:assert/strict';
import {createWheelAdmission}
  from './web/tests/browser/support/native-wheel-completion.mjs';
const start={top:3685,extent:4185,height:500,
  origin:24,padding:24,room:272.125,anchor:-30,left:20,width:760};
function operation({delta=-120, geometry=start, decide, tail={}}={}) {
  const admission=createWheelAdmission({token:'one',thread:'original',decide});
  let ms=0, callbacks=0, baseline;
  const receipt=(kind,g=geometry,extra={})=>({kind,token:'one',thread:'original',ms:++ms,
    geometry:g,same:true,...extra});
  const send=(kind,g=geometry,extra={})=>admission.receive(receipt(kind,g,extra));
  const consume=(g=geometry)=>admission.consume(receipt('consume',g),value=>{
    callbacks++;baseline=value;
  });
  send('wheel',geometry,{trusted:true,delta,deltaX:0,deltaMode:0});
  send('post-handler',geometry,{prevented:false,preDefault:true,...tail});
  return {admission,send,consume,receipt,get callbacks(){return callbacks;},
    get baseline(){return baseline;}};
}
function stable(op,g) {op.send('scrollend',g,{trusted:true});
  for(let i=0;i<3;i++)op.send('frame',g);}
function earlyReplay(decide) {
  const op=operation({decide});
  op.send('scrollend',start,{trusted:true});
  op.send('frame',start);
  op.consume(start);
  assert.equal(op.callbacks,0,'early scrollend must keep the real baseline callback blocked');
  const moved={...start,top:3565,anchor:90};
  op.send('scroll',moved,{trusted:true});
  assert.equal(op.consume(moved),false,'displacement without completion stays pending');
  const retired={...moved,extent:4065,room:152.125};
  op.send('style',retired,{before:moved});
  stable(op,retired);
  assert.equal(op.consume(retired),true);
  assert.deepEqual(op.baseline,retired);
  assert.equal(op.consume(retired),false);
  assert.equal(op.callbacks,1);
}
earlyReplay();
// The actual admission wrapper runs with the former any-trusted-scrollend
// decision. The same regression must detect that callback's premature escape.
function formerDecision(s,r) {
  if(r.kind==='scrollend'&&r.trusted){s.completion=r.geometry;s.last=r.geometry;s.intervals=2;}
}
assert.throws(()=>earlyReplay(formerDecision),/early scrollend must keep/);
for (const [kind,extra] of [['scroll',{thread:'replacement'}],['scroll',{token:'old'}],['input',{}],
  ['scroll',{same:false}]]) {
  const op=operation();op.send(kind,start,extra);
  assert(op.admission.state.failure);assert.equal(op.consume(),false);
}
for(const extra of [{prevented:true,preDefault:true},{prevented:false,preDefault:false}]) {
  const op=operation({tail:extra});
  assert(op.admission.state.failure);assert.equal(op.consume(),false);
}
const moved={...start,top:3565,anchor:90};
const write=operation();write.send('write',moved,{before:start});
assert.match(write.admission.state.failure.reason,/BEHAVIOR_WHEEL_CANCELLATION_WRITE/);
assert.equal(write.consume(moved),false);
// A tail shrink can clamp the viewport to the numerical wheel destination.
// That displacement belongs to the style mutation, never the native wheel.
const clamp=operation();
const clamped={...moved,extent:4065,room:152.125};
clamp.send('style',clamped,{before:start});clamp.send('scroll',clamped,{trusted:true});
stable(clamp,clamped);assert.equal(clamp.consume(clamped),false);
assert.match(clamp.admission.state.failure.reason,/GEOMETRY/);
// A pending -8px inset cannot justify a -9px clamp or unrelated write.
for(const kind of ['style','write']){
  const admission=createWheelAdmission({token:'one',thread:'original'});
  let ms=0,callbacks=0;
  const receipt=(kind,geometry,extra={})=>({kind,geometry,token:'one',thread:'original',
    ms:++ms,same:true,...extra});
  const initial={...start,top:149,origin:32,padding:32,extent:985,room:408,anchor:4};
  const padding={...initial,origin:24,padding:24,extent:977,anchor:-4};
  admission.receive(receipt('wheel',initial,{trusted:true,delta:-160,deltaMode:0,deltaX:0}));
  admission.receive(receipt('style',padding,{before:initial}));
  const wrong=kind==='style'?{...padding,top:140,extent:640,room:71.25,anchor:5}
    :{...padding,top:140,anchor:5};
  admission.receive(receipt(kind,wrong,{before:padding,method:'scrollTop'}));
  assert.match(admission.state.failure.reason,/CANCELLATION_(GEOMETRY|WRITE)/);
  assert.equal(admission.consume(receipt('consume',wrong),()=>callbacks++),false);
  assert.equal(callbacks,0);
}
const smooth=operation();
smooth.send('write',start,{before:start,method:'scrollTo',args:[{top:3565,behavior:'smooth'}]});
smooth.send('scroll',moved,{trusted:true});stable(smooth,moved);
assert.equal(smooth.consume(moved),false);
assert.match(smooth.admission.state.failure.reason,/CANCELLATION_WRITE/);
const replacedLine=operation();
replacedLine.send('frame',{...start,anchor:470});
replacedLine.send('scroll',{...moved,anchor:590},{trusted:true});stable(replacedLine,moved);
assert.equal(replacedLine.consume(moved),false);
assert.match(replacedLine.admission.state.failure.reason,/CANCELLATION_ANCHOR/);
const beforeHandler=createWheelAdmission({token:'one',thread:'original'});
let n=0;
const tailReceipt=(kind,g,extra={})=>({kind,token:'one',thread:'original',ms:++n,
  geometry:g,same:true,...extra});
beforeHandler.receive(tailReceipt('wheel',start,{trusted:true,delta:-120,deltaX:0,deltaMode:0}));
const hijacked={...start,top:3600,anchor:55};
beforeHandler.receive(tailReceipt('write',hijacked,{before:start,method:'scrollTop',value:3600}));
beforeHandler.receive(tailReceipt('post-handler',hijacked,{preDefault:true,prevented:false}));
assert.match(beforeHandler.state.failure.reason,/CANCELLATION_WRITE/);
const unexplained=operation();unexplained.send('scroll',{...moved,origin:30},{trusted:true});
assert.match(unexplained.admission.state.failure.reason,/UNEXPLAINED/);
const late=operation();late.send('scroll',moved,{trusted:true});stable(late,moved);
late.send('scroll',{...moved,top:3566,anchor:89},{trusted:true});
assert.match(late.admission.state.failure.reason,/AFTER_COMPLETION/);
assert.equal(late.consume(moved),false);
const travel=operation();travel.send('frame',{...start,top:3687,anchor:-32});
const firstFailure=travel.admission.state.failure;
assert.match(firstFailure.reason,/BEHAVIOR_WHEEL_CANCELLATION_TRAVEL/);
travel.send('scroll',moved,{trusted:true});stable(travel,moved);
assert.equal(travel.admission.state.failure,firstFailure);assert.equal(travel.consume(moved),false);
// Fractional insets are a measured coordinate correction, never native movement.
const inset=operation();inset.send('scroll',moved,{trusted:true});
const insetChanged={...moved,origin:14,padding:14,extent:4175,anchor:80};
inset.send('style',insetChanged,{before:moved});
const corrected={...insetChanged,top:3555,anchor:90};
inset.send('write',corrected,{before:insetChanged});stable(inset,corrected);
assert.equal(inset.consume(corrected),true);
assert.equal(inset.callbacks,1);
for(const delta of [-1,-100000]) {
  const op=operation({delta});
  op.send('scrollend',start,{trusted:true});for(let i=0;i<3;i++)op.send('frame',start);
  assert.equal(op.consume(start),false,'no-snap tolerance must never admit absent movement');
  const top=Math.max(0,start.top+delta),g={...start,top,anchor:start.anchor+start.top-top};
  op.send('scroll',g,{trusted:true});stable(op,g);assert.equal(op.consume(g),true);
}
const boundary=operation({geometry:{...start,top:0},delta:-120});
assert.match(boundary.admission.state.failure.reason,/BOUNDARY_NOOP/);
const absent=operation();absent.send('frame',start,{ms:5001});
assert.match(absent.admission.state.failure.reason,/DEADLINE/);assert.equal(absent.consume(),false);
const consumptionDeadline=operation();
consumptionDeadline.send('scroll',moved,{trusted:true});stable(consumptionDeadline,moved);
consumptionDeadline.admission.receive(consumptionDeadline.receipt('query',moved,{ms:5001}));
assert.equal(consumptionDeadline.consume(moved),false,'consumption shares the dispatch deadline');
const twoIntervals=operation();twoIntervals.send('scroll',moved,{trusted:true});
twoIntervals.send('scrollend',moved,{trusted:true});
twoIntervals.send('frame',moved);twoIntervals.send('frame',moved);
assert.equal(twoIntervals.consume(moved),false);
twoIntervals.send('frame',moved);assert.equal(twoIntervals.consume(moved),true);
console.log('Operation-specific wheel baseline replay passed; former decision failed as required');
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", probe], cwd=root,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "former decision failed as required" in result.stdout


@pytest.mark.real_provider_homes
def test_late_created_room_is_observed_before_native_wheel() -> None:
    """A room created by acceptance after arm needs synchronous setter receipts."""
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[2]
    source = (root / "web/tests/browser/support/native-wheel-completion.mjs").read_text()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 900, "height": 700})
            page.evaluate("""async source=>{
              const url=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));
              window.wheelModule=await import(url);
              URL.revokeObjectURL(url);
            }""", source)
            for mode in ("consume", "callback-error", "replacement"):
                page.set_content("""<div data-conversation-thread style="box-sizing:border-box;
                  height:500px;width:760px;overflow:auto;display:flex;flex-direction:column;
                  padding:32px 0 12px"><div data-conversation-content style="flex:none;
                  position:relative;height:533px"><div data-conversation-view-key="line"
                  style="position:absolute;top:120px;height:40px">Reading line</div></div></div>""")
                page.evaluate("""mode=>{
                  const t=document.querySelector('[data-conversation-thread]');
                  const method=CSSStyleDeclaration.prototype.setProperty;
                  const observer=window.wheelModule.observeNativeWheel(t);
                  // This is the actual arm-before-acceptance order in the case.
                  const room=document.createElement('div');
                  room.dataset.conversationReservedSpace='';
                  room.style.cssText='height:408px;flex:none;overflow-anchor:none';
                  t.append(room);t.scrollTop=149;
                  t.addEventListener('wheel',()=>{
                    t.style.paddingTop='24px';
                    if(mode==='replacement'){
                      const replacement=room.cloneNode();room.replaceWith(replacement);
                      replacement.style.height='72.25px';
                    }else room.style.height='72.25px';
                  },{once:true});
                  window.nativeProbe={observer,t,room,method,mode,callbacks:0};
                }""", mode)
                page.locator("[data-conversation-thread]").hover()
                page.mouse.wheel(0, -160)
                page.wait_for_function("""()=>{
                  const status=window.nativeProbe.observer.status();
                  return status.ready||status.failure;
                }""")
                proof = page.evaluate("""()=>{
                  const p=window.nativeProbe,status=p.observer.status();
                  let callbackError=null;
                  if(!status.failure){
                    try{p.observer.consume(()=>{
                      p.callbacks++;
                      if(p.mode==='callback-error')throw new Error('baseline callback failed');
                    });}catch(error){callbackError=error.message;}
                  }else p.observer.cleanup();
                  p.observer.cleanup();
                  const styles=status.receipts.filter(r=>r.kind==='style');
                  const height=styles.find(r=>r.before.room===408&&r.geometry.room===72.25);
                  const before=status.receipts.find(r=>r.kind==='wheel');
                  const after=status.receipts.find(r=>r.kind==='post-handler');
                  const baseline=status.receipts.find(r=>r.kind==='baseline');
                  const roomDescriptor=Object.getOwnPropertyDescriptor(p.room.style,'height');
                  return {failure:status.failure,callbacks:p.callbacks,callbackError,height,
                    captured:before.geometry,postHandler:after.geometry,
                    baseline:baseline?.geometry,
                    scrollRestored:!Object.hasOwn(p.t,'scrollTop'),
                    styleRestored:!roomDescriptor.set&&roomDescriptor.writable===true,
                    methodRestored:CSSStyleDeclaration.prototype.setProperty===p.method};
                }""")
                assert proof["scrollRestored"] and proof["styleRestored"]
                assert proof["methodRestored"]
                if mode == "replacement":
                    assert proof["failure"]["reason"] == "SETUP_WHEEL_IDENTITY", proof
                    assert proof["callbacks"] == 0, proof
                    continue
                assert proof["failure"] is None, proof
                assert proof["callbacks"] == 1, proof
                assert proof["height"]["before"]["top"] == 149, proof
                assert proof["height"]["geometry"]["top"] == 141, proof
                assert proof["captured"]["origin"] == 32, proof
                assert proof["postHandler"]["origin"] == 24, proof
                assert proof["captured"]["anchor"] == proof["postHandler"]["anchor"], proof
                assert proof["baseline"]["top"] == 0, proof
                if mode == "callback-error":
                    assert proof["callbackError"] == "baseline callback failed", proof
                else:
                    assert proof["callbackError"] is None, proof
        finally:
            browser.close()
