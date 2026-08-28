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
  const [liveLoaded, setLiveLoaded] = useState(false);
  const [posterTick, setPosterTick] = useState(0);
  const [posterOk, setPosterOk] = useState(false);

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

  // The live <img> unmounts whenever active goes false (see below). Drop the
  // "live frame arrived" flag so the badge doesn't flash stale, and pull a
  // fresh snapshot so the still we fall back to isn't minutes old.
  useEffect(() => {
    if (!active) {
      setLiveLoaded(false);
      setPosterTick((n) => n + 1);
    }
  }, [active]);

  const streamSrc = `/api/cameras/${printerId}/stream?retry=${retryKey}`;
  // No cache-buster on the first paint (posterTick 0) so a recent snapshot
  // is served straight from the HTTP cache — that's what makes returning to
  // the dashboard show the last frame instantly instead of a blank box.
  const posterSrc = `/api/cameras/${printerId}/snapshot${posterTick ? `?t=${posterTick}` : ""}`;

  const liveShowing = active && liveLoaded && !failed;
  const showPoster = posterOk && !active;
  const showLoading = active && !liveShowing;

  const body = (
    <div className={`camera-stage${posterOk || liveLoaded ? " has-frame-dimensions" : ""}`}>
      {expanded ? (
        <img
          className={`camera-poster${showPoster ? "" : " is-hidden"}`}
          src={posterSrc}
          alt=""
          aria-hidden="true"
          onLoad={() => setPosterOk(true)}
          onError={() => setPosterOk(false)}
        />
      ) : null}
      {active ? (
        <img
          className={`camera-feed${liveLoaded && !failed ? " is-live" : ""}`}
          src={streamSrc}
          alt={`${printerName} 실시간 카메라`}
          onLoad={() => { attemptsRef.current = 0; setFailed(false); setLiveLoaded(true); }}
          onError={() => {
            setFailed(true);
            setLiveLoaded(false);
            attemptsRef.current += 1;
            const delay = Math.min(1000 * 2 ** (attemptsRef.current - 1), 30000);
            retryRef.current = window.setTimeout(() => {
              setRetryKey((current) => current + 1);
            }, delay);
          }}
        />
      ) : null}
      {active && liveLoaded && !failed ? (
        <span className="camera-live-badge">
          <span className="camera-live-dot" aria-hidden="true" />
          LIVE
        </span>
      ) : null}
      {showLoading ? (
        <span className="camera-connecting-skeleton" role="status">
          <span className="sr-only">카메라 연결 중</span>
        </span>
      ) : null}
    </div>
  );

  if (collapsible) {
    return (
      <details
        className="camera-details"
        ref={rootRef as React.RefObject<HTMLDetailsElement>}
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary><Camera size={14} /> 카메라</summary>
        {body}
      </details>
    );
  }

  return <div className="camera-live" ref={rootRef as React.RefObject<HTMLDivElement>}>{body}</div>;
}
