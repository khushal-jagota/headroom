"""The live admission callback cannot escape on another operation's early scrollend."""
from __future__ import annotations

import subprocess
from pathlib import Path


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
