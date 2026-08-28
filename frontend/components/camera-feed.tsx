"use client";
/* eslint-disable @next/next/no-img-element */

import { Camera } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export function CameraFeed({
  printerId,
  printerName,
  collapsible = false,
}: {
  printerId: number;
  printerName: string;
  collapsible?: boolean;
}) {
  const rootRef = useRef<HTMLDivElement | HTMLDetailsElement>(null);
  const retryRef = useRef<number | null>(null);
  const attemptsRef = useRef(0);
  const [expanded, setExpanded] = useState(!collapsible);
  const [visible, setVisible] = useState(true);
  const [inViewport, setInViewport] = useState(true);
  const [retryKey, setRetryKey] = useState(0);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    const target = rootRef.current;
    if (!target) return;
    const observer = new IntersectionObserver(
      ([entry]) => setInViewport(entry.isIntersecting),
      { rootMargin: "160px 0px" },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  useEffect(() => () => {
    if (retryRef.current !== null) window.clearTimeout(retryRef.current);
  }, []);

  const active = expanded && visible && inViewport;

  // The <img> unmounts whenever active goes false (see the ternary below), so
  // a fresh one mounts next time without re-firing onLoad until a real frame
  // arrives — reset here so the badge doesn't show stale "LIVE" the instant
  // it reappears.
  useEffect(() => {
    if (!active) setLoaded(false);
  }, [active]);

  const stream = `/api/cameras/${printerId}/stream?retry=${retryKey}`;
  const image = active ? (
    <div className="camera-stage">
      <img
        className="camera-feed"
        src={stream}
        alt={`${printerName} 실시간 카메라`}
        onLoad={() => { attemptsRef.current = 0; setFailed(false); setLoaded(true); }}
        onError={() => {
          setFailed(true);
          setLoaded(false);
          attemptsRef.current += 1;
          const delay = Math.min(1000 * 2 ** (attemptsRef.current - 1), 30000);
          retryRef.current = window.setTimeout(() => {
            setRetryKey((current) => current + 1);
          }, delay);
        }}
      />
      {loaded && !failed ? (
        <span className="camera-live-badge">
          <span className="camera-live-dot" aria-hidden="true" />
          LIVE
        </span>
      ) : null}
      {failed ? <span className="camera-message" role="status">카메라 재연결 중</span> : null}
    </div>
  ) : <div className="camera-paused">화면에 보일 때 카메라를 연결합니다.</div>;

  if (collapsible) {
    return (
      <details
        className="camera-details"
        ref={rootRef as React.RefObject<HTMLDetailsElement>}
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary><Camera size={14} /> 카메라</summary>
        {image}
      </details>
    );
  }

  return <div className="camera-live" ref={rootRef as React.RefObject<HTMLDivElement>}>{image}</div>;
}
