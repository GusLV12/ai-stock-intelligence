"use client";

import { MotionConfig, motion } from "motion/react";

// Contenido siempre visible: el movimiento acompaña el cambio, no retrasa las cifras.
export function MotionProvider({ children }) {
  return <MotionConfig reducedMotion="user" transition={{ duration: 0.18, ease: "easeOut" }}>{children}</MotionConfig>;
}

export function MotionPanel({ children, className, ...props }) {
  return (
    <motion.div className={className} initial={{ opacity: 0.84 }}
      animate={{ opacity: 1 }} {...props}>{children}</motion.div>
  );
}
