import { motion } from "motion/react";
import { useEffect, useRef } from "react";

export interface StreamingTextProps {
  text: string;
  showCursor?: boolean;
}

export function StreamingText({ text, showCursor = true }: StreamingTextProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [text]);

  return (
    <div ref={scrollRef} className="max-h-[50vh] overflow-y-auto scrollbar-thin">
      <p className="text-[13px] text-white/70 leading-relaxed whitespace-pre-wrap font-mono">
        {text}
        {showCursor && text.length > 0 ? (
          <motion.span
            animate={{ opacity: [1, 0] }}
            transition={{ duration: 0.5, repeat: Infinity }}
            className="inline-block w-[2px] h-[14px] bg-indigo-400 ml-0.5 align-middle"
            aria-hidden
          />
        ) : null}
      </p>
    </div>
  );
}
