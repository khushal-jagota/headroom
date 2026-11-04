import React,{useLayoutEffect,useRef,useState} from 'react';
import {motion,AnimatePresence,useReducedMotion} from 'motion/react';
import {motionTokens} from '../lib/motion-tokens';
const green='var(--accent-done)';
export function CompletionReading({done,tone,context,className=''}:{done:boolean;tone:string;context:string;className?:string}){
 const reduce=useReducedMotion();
 const container=useRef<HTMLDivElement>(null);
 const [animationRoot,setAnimationRoot]=useState<ShadowRoot|undefined>();
 useLayoutEffect(()=>{
  const root=container.current?.getRootNode();
  // Motion's exit-positioning rules must live beside the animated elements.
  setAnimationRoot(root instanceof ShadowRoot?root:undefined);
 },[]);
 const transition=reduce?{duration:0}:motionTokens.spring.smooth;
 return <div ref={container} className={'completion-content '+className} aria-live="polite">
   
   <div className="completion-headline">
    <motion.svg className="completion-glyph" viewBox="0 0 32 32" width="32" height="32" aria-hidden="true">
     <motion.rect x="3" y="3" width="26" height="26" rx="8" fill="none" initial={false} animate={{stroke:done?green:'var(--text-faintest)',fill:done?'color-mix(in srgb, var(--accent-done) 10%, transparent)':'var(--surface-1)'}} strokeWidth="1.5" transition={transition}/>
     <motion.path d="M9 16l5 5L24 11" fill="none" stroke={green} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" initial={false} animate={{pathLength:done?1:0,opacity:done?1:0}} transition={transition}/>
     <motion.path d="M12 16h8" fill="none" stroke="var(--text-faintest)" strokeWidth="1.5" strokeLinecap="round" initial={false} animate={{opacity:done?0:1}} transition={transition}/>
    </motion.svg>
    <div className="completion-word"><AnimatePresence initial={false} mode="popLayout" root={animationRoot}><motion.span key={done?'done':'open'} initial={{opacity:0,y:reduce?0:8,filter:reduce?'none':'blur(2px)'}} animate={{opacity:1,y:0,filter:'blur(0px)'}} exit={{opacity:0,y:reduce?0:-8,filter:reduce?'none':'blur(2px)'}} transition={reduce?{duration:0}:{duration:motionTokens.duration.standard,ease:motionTokens.ease.standard}}>{done?'Done':'Not done'}</motion.span></AnimatePresence></div>
   </div>
   <div className="completion-context" style={{color:tone}}><span className="status-dot" style={{background:tone}}/>{context}</div>
 </div>;
}
