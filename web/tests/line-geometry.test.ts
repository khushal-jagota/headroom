import {expect,it} from 'vitest';
import {segmentedCurve,valueAt,morphShape,comparisonAt} from '../src/lib/charts/lib/line-geometry';

it('keeps exact endpoints and phase/missing gaps through crosshair interpolation and morphs',()=>{
 const shape=segmentedCurve([1,2,20,NaN,40],[0,.25,.5,.75,1],[false,false,true,false,false],false);
 expect(shape).toEqual([[0,1],[.25,2],[.375,NaN],[.5,20],[.75,NaN],[1,40]]);
 expect(valueAt(shape,.25)).toBe(2);
 expect(valueAt(shape,.5)).toBe(20);
 expect(valueAt(shape,.3)).toBeNaN();
 expect(valueAt(shape,.6)).toBeNaN();
 expect(valueAt(shape,.9)).toBeNaN();
 const before=segmentedCurve([5,6,7,8,9],[0,.25,.5,.75,1],[],true);
 const xs=[0,.25,.3,.375,.5,.6,.75,.9,1];
 for(const progress of [0,.5,1]){
  const during=morphShape(before,shape,xs,progress);
  expect(valueAt(during,.3)).toBeNaN();
  expect(valueAt(during,.375)).toBeNaN();
  expect(valueAt(during,.6)).toBeNaN();
  expect(valueAt(during,.9)).toBeNaN();
 }
 expect(valueAt(morphShape(before,shape,xs,1),.5)).toBe(20);
});

it('keeps phase smoothing separate and backend unavailable comparisons neutral',()=>{
 const shape=segmentedCurve([1,2,100,101],[0,1/3,2/3,1],[false,false,true,false],true);
 expect(valueAt(shape,1/3)).toBe(2);
 expect(valueAt(shape,2/3)).toBe(100);
 expect(valueAt(shape,.5)).toBeNaN();
 expect(comparisonAt([[0,true],[.5,null],[1,false]],.5)).toBeNull();
 expect(comparisonAt([[0,true],[.5,null],[1,false]],.98)).toBe(false);
 expect(comparisonAt([],0)).toBeNull();
});
