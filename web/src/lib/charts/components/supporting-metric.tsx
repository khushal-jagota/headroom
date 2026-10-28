import React,{useId} from 'react';
import {motion,useReducedMotion} from 'motion/react';
import './supporting-metric.css';
import {motionTokens} from '../lib/motion-tokens';
export function SupportingMetric({title,reading,status,open,onToggle,children}:{title:string;reading:React.ReactNode;status:React.ReactNode;open:boolean;onToggle:()=>void;children:React.ReactNode}){
 const id=useId(),reduce=useReducedMotion();
 return <article className="support-disclosure" data-expanded={open}>
  <button className="support-summary" aria-expanded={open} aria-controls={id} onClick={onToggle}>
   <span className="support-title-text">{title}</span>
   <span className="support-reading">{reading}</span>
   <span className="support-status">{status}</span>
   <motion.svg className="support-chevron" viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" initial={false} animate={{rotate:open?180:0}} transition={{duration:reduce?0:motionTokens.duration.standard,ease:motionTokens.ease.standard}}><path d="m5.5 7.5 4.5 4.5 4.5-4.5"/></motion.svg>
  </button>
  <motion.div className="support-reveal" id={id} role="region" aria-label={title+' chart'} aria-hidden={!open} inert={!open} initial={false} animate={{height:open?'auto':0,opacity:open?1:0}} transition={reduce?{duration:0}:{height:motionTokens.spring.disclosure,opacity:{duration:open?motionTokens.duration.standard:motionTokens.duration.instant,delay:open?motionTokens.duration.disclosureDelay:0}}}>
   <div className="support-chart-body">{children}</div>
  </motion.div>
 </article>;
}
