"use client";

import Image from "next/image";
import { Box, ImageIcon, X } from "lucide-react";
import { useState } from "react";

import type { Job } from "@/lib/types";
import { StlViewer } from "./stl-viewer";

export function JobPreview({ job, admin = false }: { job: Job; admin?: boolean }) {
  const [open, setOpen] = useState(false);
  const base = admin ? "/api/admin/jobs" : "/api/jobs";
  const imageUrl = `${base}/${job.id}/${admin ? "3mf-thumb" : "thumb"}`;
  const stlUrl = `${base}/${job.id}/${admin ? "stl" : "stl-preview"}`;
  return (
    <>
      <button className="button button-ghost button-small" onClick={() => setOpen(true)}>
        {job.preview_kind === "image" ? <ImageIcon size={14} /> : <Box size={14} />} 미리보기
      </button>
      {open ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setOpen(false)}>
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby={`preview-${job.id}`}>
            <header className="modal-header">
              <h2 id={`preview-${job.id}`} className="truncate">{job.filename}</h2>
              <button className="icon-button" onClick={() => setOpen(false)} aria-label="닫기"><X size={18} /></button>
            </header>
            <div className="modal-body preview-body">
              {job.preview_kind === "image" ? (
                <div className="thumb-stage">
                  <Image src={imageUrl} alt={`${job.filename} 미리보기`} fill unoptimized sizes="(max-width: 768px) 100vw, 700px" />
                </div>
              ) : <StlViewer url={stlUrl} />}
              <p className="viewer-hint">{job.preview_kind === "image" ? "Bambu Studio 슬라이싱 미리보기" : "드래그로 회전 · 스크롤로 확대"}</p>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
