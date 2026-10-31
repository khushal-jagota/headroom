import type { ComponentPropsWithoutRef, HTMLAttributes, ReactNode } from 'react';
import { motion, type SVGMotionProps } from 'motion/react';
import { gaugeArc } from '../lib/gauge-geometry';
import styles from './gauge-visual.module.css';

interface GaugeVisualProps {
  className: string;
  trackClassName: string;
  fillClassName: string;
  readoutClassName: string;
  svgProps: ComponentPropsWithoutRef<'svg'>;
  readoutProps?: Omit<HTMLAttributes<HTMLDivElement>, 'className' | 'children'>;
  fillProps: Omit<SVGMotionProps<SVGPathElement>, 'className'>;
  coordinatePrecision?: 3;
  decorations?: ReactNode;
  children: ReactNode;
}

/** Drawing and positioning only. The adapter owns motion, semantics, and actions. */
export function GaugeVisual({ className, trackClassName, fillClassName, readoutClassName, svgProps, readoutProps, fillProps, coordinatePrecision, decorations, children }: GaugeVisualProps) {
  const track = gaugeArc(1, coordinatePrecision);
  return <div className={`${styles.visual} ${className}`}>
    <svg viewBox="0 0 100 90" {...svgProps}>
      <path className={`${styles.stroke} ${trackClassName}`} d={track} />
      <motion.path className={`${styles.stroke} ${fillClassName}`} d={track} {...fillProps} />
      {decorations}
    </svg>
    <div className={`${styles.readout} ${readoutClassName}`} {...readoutProps}>{children}</div>
  </div>;
}
