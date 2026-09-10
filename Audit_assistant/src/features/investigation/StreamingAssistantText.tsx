import { useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

const STREAM_CHARACTER_INTERVAL_MS = 18;
const MAX_STREAMED_CHARACTER_COUNT = 600;
const MAX_STREAM_TICKS = 180;

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
  const canStream = shouldStream
    && !reduceMotion
    && characters.length <= MAX_STREAMED_CHARACTER_COUNT;
  const onCompleteRef = useRef(onComplete);
  const [visibleCharacterCount, setVisibleCharacterCount] = useState(
    canStream ? 0 : characters.length
  );

  onCompleteRef.current = onComplete;

  useLayoutEffect(() => {
    if (!canStream) {
      setVisibleCharacterCount(characters.length);
      if (shouldStream) onCompleteRef.current?.();
      return;
    }

    let nextCharacterCount = 0;
    const charactersPerTick = Math.max(1, Math.ceil(characters.length / MAX_STREAM_TICKS));
    setVisibleCharacterCount(0);
    const intervalId = window.setInterval(() => {
      nextCharacterCount = Math.min(
        nextCharacterCount + charactersPerTick,
        characters.length
      );
      setVisibleCharacterCount(nextCharacterCount);
      if (nextCharacterCount === characters.length) {
        window.clearInterval(intervalId);
        onCompleteRef.current?.();
      }
    }, STREAM_CHARACTER_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [canStream, shouldStream, text]);

  const isStreaming = canStream && visibleCharacterCount < characters.length;
  const visibleText = characters.slice(0, visibleCharacterCount).join("");

  return (
    <div className={className} aria-label={text} data-streaming={isStreaming ? "true" : "false"}>
      <div className={`inv-streaming-copy${isStreaming ? " is-streaming" : ""}`} aria-hidden="true">
        <ReactMarkdown>{visibleText}</ReactMarkdown>
      </div>
    </div>
  );
}
