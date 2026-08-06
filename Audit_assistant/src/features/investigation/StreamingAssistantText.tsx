import { useLayoutEffect, useRef, useState } from "react";

const STREAM_CHARACTER_INTERVAL_MS = 18;

interface StreamingAssistantTextProps {
  text: string;
  shouldStream: boolean;
  className: string;
  onComplete?: () => void;
}

export function StreamingAssistantText({
  text,
  shouldStream,
  className,
  onComplete
}: StreamingAssistantTextProps) {
  const characters = Array.from(text);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const onCompleteRef = useRef(onComplete);
  const [visibleCharacterCount, setVisibleCharacterCount] = useState(
    shouldStream && !reduceMotion ? 0 : characters.length
  );

  onCompleteRef.current = onComplete;

  useLayoutEffect(() => {
    if (!shouldStream) {
      setVisibleCharacterCount(characters.length);
      return;
    }

    if (reduceMotion) {
      setVisibleCharacterCount(characters.length);
      onCompleteRef.current?.();
      return;
    }

    let nextCharacterCount = 0;
    setVisibleCharacterCount(0);
    const intervalId = window.setInterval(() => {
      nextCharacterCount = Math.min(nextCharacterCount + 1, characters.length);
      setVisibleCharacterCount(nextCharacterCount);
      if (nextCharacterCount === characters.length) {
        window.clearInterval(intervalId);
        onCompleteRef.current?.();
      }
    }, STREAM_CHARACTER_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [reduceMotion, shouldStream, text]);

  const isStreaming = shouldStream && !reduceMotion && visibleCharacterCount < characters.length;

  return (
    <div className={className} aria-label={text} data-streaming={isStreaming ? "true" : "false"}>
      <span className={`inv-streaming-copy${isStreaming ? " is-streaming" : ""}`} aria-hidden="true">
        {characters.slice(0, visibleCharacterCount).join("")}
      </span>
    </div>
  );
}
