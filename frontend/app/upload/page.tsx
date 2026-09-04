"use client";

import { Box, Check, CheckCircle2, FileArchive, FileUp, LayoutGrid, RotateCcw, RotateCw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import posthog from "posthog-js";

import {
  BED_MM,
  StlPlateEditor,
  StlViewer,
  type ModelTransform,
  type PartMetrics,
  type PartTransform,
} from "@/components/stl-viewer";
import { api, formatBytes } from "@/lib/api";
import { uploadSizeError } from "@/lib/upload-limits";
import type { Printer } from "@/lib/types";

type PreviewFile = { file: File; name: string };
type PreviewData = { files: PreviewFile[]; printers: Printer[] };
type Dimensions = { x: number; y: number; z: number };
const initialTransform: ModelTransform = { scale: 1, rotationX: 0, rotationY: 0, rotationZ: 0 };

// Which printer the backend's pick_best_printer would land on for a blank
// (자동 배정) choice: the healthy printer with the shortest queue, mirroring
// the server logic so we can still show a filament palette under 자동 배정.
function predictAutoPrinter(printers: Printer[]): Printer | undefined {
  if (!printers.length) return undefined;
  const healthy = printers.filter((printer) => printer.status !== "offline" && printer.status !== "error");
  const pool = healthy.length ? healthy : printers;
  return pool.reduce(
    (best, printer) => ((printer.queue_count ?? 0) < (best.queue_count ?? 0) ? printer : best),
    pool[0],
  );
}

// hex of the currently picked filament slot — feeds the 3D viewer's model
// colour. undefined = 선호 없음, viewer falls back to the theme token.
function selectedFilamentHex(printers: Printer[], printerId: string, slotIndex: string): string | undefined {
  if (!slotIndex) return undefined;
  const printer = printers.find((item) => String(item.id) === printerId) ?? predictAutoPrinter(printers);
  const slot = printer?.slots?.find((item) => String(item.slot_index) === slotIndex && !item.is_empty);
  return slot?.color_hex ?? undefined;
}

// Shared by both submission paths (direct .gcode.3mf upload and the STL
// confirm step). Printer choice is optional — blank routes through the
// predicted auto-assign printer above so a colour can still be picked. The
// colour list is a radio group of chips: a swatch (with a theme-flipping
// outline, so black-on-dark / white-on-light stays visible) plus the name.
function PrinterFilamentPicker({
  printers,
  printerId,
  onPrinterChange,
  slotIndex,
  onSlotChange,
}: {
  printers: Printer[];
  printerId: string;
  onPrinterChange: (value: string) => void;
  slotIndex: string;
  onSlotChange: (value: string) => void;
}) {
  const groupName = useId();
  const explicit = printers.find((printer) => String(printer.id) === printerId);
  const autoPrinter = printerId ? undefined : predictAutoPrinter(printers);
  const effectivePrinter = explicit ?? autoPrinter;
  const loadedSlots = effectivePrinter?.slots?.filter((item) => !item.is_empty) ?? [];

  return (
    <div className="picker-row">
      <div className="field">
        <label htmlFor="printer-choice">프린터</label>
        <select
          id="printer-choice"
          className="select"
          value={printerId}
          onChange={(event) => { onPrinterChange(event.target.value); onSlotChange(""); }}
        >
          <option value="">자동 배정 (대기열이 가장 적은 프린터)</option>
          {printers.map((printer) => (
            <option key={printer.id} value={printer.id}>{printer.name} · 대기 {printer.queue_count ?? 0}건</option>
          ))}
        </select>
      </div>
      <div className="field">
        <span className="field-label filament-label-row">
          필라멘트 색상
          {autoPrinter ? <span className="field-hint">{autoPrinter.name} 기준</span> : null}
        </span>
        {!effectivePrinter ? (
          <p className="filament-empty">프린터가 선택되지 않음</p>
        ) : loadedSlots.length === 0 ? (
          <p className="filament-empty">로드된 필라멘트가 없음</p>
        ) : (
          <div className="filament-options" role="radiogroup" aria-label="필라멘트 색상">
            <label className={`filament-chip${slotIndex === "" ? " is-selected" : ""}`}>
              <input
                type="radio"
                className="sr-only"
                name={groupName}
                checked={slotIndex === ""}
                onChange={() => onSlotChange("")}
              />
              <span className="filament-dot filament-dot-none" aria-hidden="true" />
              <span>선호 없음</span>
            </label>
            {loadedSlots.map((item) => {
              const value = String(item.slot_index);
              return (
                <label key={item.id} className={`filament-chip${slotIndex === value ? " is-selected" : ""}`}>
                  <input
                    type="radio"
                    className="sr-only"
                    name={groupName}
                    checked={slotIndex === value}
                    onChange={() => onSlotChange(value)}
                  />
                  <span
                    className="filament-dot"
                    style={{ "--filament": item.color_hex ?? "transparent" } as React.CSSProperties}
                    aria-hidden="true"
                  />
                  <span>
                    {item.color_name ?? "색상 미확인"} · {item.material_type ?? "재질 미확인"}
                    <small> 슬롯 {item.slot_index + 1}</small>
                  </span>
                </label>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function FilePicker({ id, accept, acceptAttribute, files, onFiles, title, hint }: { id: string; accept: (file: File) => boolean; acceptAttribute: string; files: File[]; onFiles: (files: File[]) => void; title: string; hint: string }) {
  const add = (incoming: FileList | File[]) => onFiles([...files, ...Array.from(incoming).filter(accept).filter((file) => !files.some((item) => item.name === file.name && item.size === file.size))]);
  return (
    <div className="file-picker" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); add(event.dataTransfer.files); }}>
      <input id={id} type="file" accept={acceptAttribute} multiple className="sr-only" onChange={(event) => event.target.files && add(event.target.files)} />
      <label htmlFor={id} className="file-picker-target"><FileUp size={24} /><strong>{title}</strong><span>{hint}</span></label>
      {files.length ? <div className="selected-files">{files.map((file, index) => <div key={`${file.name}-${index}`}><span className="truncate">{file.name}</span><small>{formatBytes(file.size)}</small><button className="icon-button" onClick={() => onFiles(files.filter((_, item) => item !== index))} aria-label={`${file.name} 제거`}><X size={15} /></button></div>)}</div> : null}
    </div>
  );
}

function SubmissionSuccessDialog({ onContinue }: { onContinue: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const continueRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    continueRef.current?.focus();
    return () => dialog.close();
  }, []);

  return (
    <dialog
      className="modal submission-dialog"
      ref={dialogRef}
      aria-labelledby="submission-success-title"
      onCancel={(event) => { event.preventDefault(); onContinue(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onContinue(); }}
    >
      <div className="modal-body submission-dialog-body">
        <div className="submission-dialog-message">
          <div className="submission-success-icon" aria-hidden="true"><CheckCircle2 size={20} /></div>
          <div>
            <h2 id="submission-success-title">출력 신청이 완료되었습니다</h2>
            <p>관리자가 신청을 승인하면 학교 이메일로 안내를 보내드립니다.</p>
            <p>출력 승인/거부, 완료 시 이메일로 안내되니 메일함을 수시로 확인해주세요.</p>
          </div>
        </div>
        <button ref={continueRef} className="button button-primary button-full" onClick={onContinue}>내 작업에서 확인</button>
      </div>
    </dialog>
  );
}

function normalizeAngle(deg: number): number {
  const r = Math.round(deg) % 360;
  return r < 0 ? r + 360 : r;
}

/** The 0° readout in the rotation grid: click to type an exact angle, or grab
 * and spin it like a rotation knob in a 3D app. */
function AngleDial({ value, onChange }: { value: number; onChange: (deg: number) => void }) {
  const [editing, setEditing] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const drag = useRef<{ last: number; acc: number; travel: number } | null>(null);

  useEffect(() => { if (editing) inputRef.current?.select(); }, [editing]);

  const pointerAngle = (event: React.PointerEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return 0;
    return (
      (Math.atan2(event.clientY - (rect.top + rect.height / 2), event.clientX - (rect.left + rect.width / 2)) * 180) /
      Math.PI
    );
  };
  const onPointerDown = (event: React.PointerEvent) => {
    if (editing) return;
    drag.current = { last: pointerAngle(event), acc: value, travel: 0 };
    wrapRef.current?.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const now = pointerAngle(event);
    let step = now - d.last;
    if (step > 180) step -= 360;
    else if (step < -180) step += 360;
    d.last = now;
    d.acc += step;
    d.travel += Math.abs(step);
    onChange(normalizeAngle(d.acc));
  };
  const onPointerUp = (event: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    try { wrapRef.current?.releasePointerCapture(event.pointerId); } catch { /* ignore */ }
    if (d && d.travel < 4) setEditing(true);
  };
  const commit = (raw: string) => {
    const n = Number(raw);
    if (Number.isFinite(n)) onChange(normalizeAngle(n));
    setEditing(false);
  };

  return (
    <div
      ref={wrapRef}
      className="angle-dial"
      style={{ "--angle": `${value}deg` } as React.CSSProperties}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      title="드래그로 회전 · 클릭해서 각도 입력"
    >
      {editing ? (
        <input
          ref={inputRef}
          type="number"
          defaultValue={Math.round(value)}
          onBlur={(event) => commit(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") commit((event.target as HTMLInputElement).value);
            if (event.key === "Escape") setEditing(false);
          }}
        />
      ) : (
        <strong>{Math.round(value)}°</strong>
      )}
    </div>
  );
}

function StlWorkbench({ data, onBack }: { data: PreviewData; onBack: () => void }) {
  const router = useRouter();
  const [selected, setSelected] = useState(0);
  const [transforms, setTransforms] = useState<ModelTransform[]>(data.files.map(() => ({ ...initialTransform })));
  const [dimensions, setDimensions] = useState<Record<number, Dimensions>>({});
  const [triangles, setTriangles] = useState<Record<number, number>>({});
  const [notes, setNotes] = useState("");
  const [printerId, setPrinterId] = useState("");
  const [slotIndex, setSlotIndex] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  // The picked files are previewed straight from the browser — no upload until
  // 출력 신청 below. One blob URL per file, revoked when the workbench closes.
  const [objectUrls] = useState(() => data.files.map((entry) => URL.createObjectURL(entry.file)));
  useEffect(() => () => objectUrls.forEach((url) => URL.revokeObjectURL(url)), [objectUrls]);
  // ← / → switch files (unless a form field has focus).
  useEffect(() => {
    if (data.files.length < 2) return;
    const onKey = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (event.key === "ArrowLeft") setSelected((index) => Math.max(0, index - 1));
      if (event.key === "ArrowRight") setSelected((index) => Math.min(data.files.length - 1, index + 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [data.files.length]);
  const current = transforms[selected];
  const size = dimensions[selected];
  const scaled = size ? { x: size.x * current.scale, y: size.y * current.scale, z: size.z * current.scale } : null;
  const tooLarge = scaled ? Math.max(scaled.x, scaled.y, scaled.z) > 256 : false;
  const setCurrent = (patch: Partial<ModelTransform>) => setTransforms((items) => items.map((item, index) => index === selected ? { ...item, ...patch } : item));
  const setDimension = (axis: keyof Dimensions, value: number) => {
    if (!size || value <= 0) return;
    setCurrent({ scale: value / size[axis] });
  };
  const confirm = async () => {
    const sizeError = uploadSizeError(data.files.map((item) => item.file));
    if (sizeError) { setError(sizeError); return; }
    setBusy(true); setError("");
    const form = new FormData();
    data.files.forEach((entry, index) => {
      form.append("files", entry.file);
      form.append("scales", String(transforms[index].scale));
      form.append("rotations_x", String(transforms[index].rotationX));
      form.append("rotations_y", String(transforms[index].rotationY));
      form.append("rotations_z", String(transforms[index].rotationZ));
    });
    form.append("user_notes", notes);
    form.append("printer_id", printerId);
    form.append("ams_slot", slotIndex);
    try {
      await api("/api/upload/stl-confirm", { method: "POST", body: form });
      posthog.capture("print_request_submitted", {
        submission_type: "stl",
        file_count: data.files.length,
        printer_selected: Boolean(printerId),
        filament_preference_selected: Boolean(slotIndex),
      });
      setSubmitted(true);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "신청하지 못했습니다."); } finally { setBusy(false); }
  };
  return (
    <div className="page">
      <header className="page-header"><div><h1>STL 크기와 방향 확인</h1><p>출력 베드 안에 들어오는지 확인한 뒤 제출하세요.</p></div><button className="button button-secondary" onClick={onBack}>파일 다시 선택</button></header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <div className="workbench-grid">
        <section className="card workbench-viewer">
          <StlViewer
            url={objectUrls[selected]}
            transform={current}
            color={selectedFilamentHex(data.printers, printerId, slotIndex)}
            onDimensions={useCallback((next: Dimensions) => setDimensions((values) => ({ ...values, [selected]: next })), [selected])}
            onStats={useCallback((next: { triangles: number }) => setTriangles((values) => ({ ...values, [selected]: next.triangles })), [selected])}
          />
          <p className="viewer-stat">
            {triangles[selected] != null ? `삼각형 ${Math.round(triangles[selected]).toLocaleString()}개 · ` : ""}
            {formatBytes(data.files[selected].file.size)}
          </p>
          <p className="viewer-hint">256 × 256mm 베드 · 드래그로 회전 · 스크롤로 확대{data.files.length > 1 ? " · ← → 파일 전환" : ""}</p>
          <div className="model-tabs">{data.files.map((file, index) => <button key={objectUrls[index]} className={selected === index ? "model-tab model-tab-active" : "model-tab"} onClick={() => setSelected(index)}>{index + 1}. {file.name}</button>)}</div>
        </section>
        <aside className="card transform-panel">
          <div className="card-header"><h2 className="truncate">{data.files[selected].name}</h2><button className="icon-button" onClick={() => setCurrent(initialTransform)} title="변형 초기화"><RotateCcw size={16} /></button></div>
          <div className="card-body">
            {scaled ? <div className={tooLarge ? "dimension-row dimension-row-error" : "dimension-row"}>{(["x", "y", "z"] as const).map((axis) => <label key={axis}><span>{axis.toUpperCase()}</span><input type="number" min="0.1" step="0.1" value={scaled[axis].toFixed(1)} onChange={(event) => setDimension(axis, Number(event.target.value))} /><small>mm</small></label>)}</div> : <div className="skeleton dimension-skeleton" />}
            {tooLarge ? <div className="notice notice-danger">출력 가능 범위 256 × 256 × 256mm를 초과합니다.</div> : null}
            <div className="field transform-field"><label htmlFor="scale">크기 · {Math.round(current.scale * 100)}%</label><input id="scale" type="range" min="1" max="400" value={Math.round(current.scale * 100)} onChange={(event) => setCurrent({ scale: Number(event.target.value) / 100 })} /></div>
            <div className="preset-row">{[25, 50, 75, 100].map((percent) => <button key={percent} className="button button-secondary button-small" onClick={() => setCurrent({ scale: percent / 100 })}>{percent}%</button>)}</div>
            <div className="rotation-grid">{(["rotationX", "rotationY", "rotationZ"] as const).map((axis) => <div key={axis}><span>{axis.slice(-1)}</span><button className="button button-secondary button-small" aria-label={`${axis.slice(-1)}축 반시계 방향 90도 회전`} title="−90°" onClick={() => setCurrent({ [axis]: normalizeAngle(current[axis] - 90) })}><RotateCcw size={15} /></button><AngleDial value={current[axis]} onChange={(deg) => setCurrent({ [axis]: deg })} /><button className="button button-secondary button-small" aria-label={`${axis.slice(-1)}축 시계 방향 90도 회전`} title="+90°" onClick={() => setCurrent({ [axis]: normalizeAngle(current[axis] + 90) })}><RotateCw size={15} /></button></div>)}</div>
            <PrinterFilamentPicker printers={data.printers} printerId={printerId} onPrinterChange={setPrinterId} slotIndex={slotIndex} onSlotChange={setSlotIndex} />
            <div className="field"><label htmlFor="notes">관리자 메모</label><textarea id="notes" className="textarea" rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder={"출력물의 목적과 용도, 관리자에게 전달할 내용을 적어주세요.\n목적이 불분명하거나 부적합하다고 판단될 경우 신청이 거부될 수 있습니다."} /></div>
            <button className={`button button-primary button-full ${busy ? "button-loading" : ""}`} disabled={busy || tooLarge} onClick={confirm}><Check size={16} /> {data.files.length}개 파일 출력 신청</button>
          </div>
        </aside>
      </div>
      {submitted ? <SubmissionSuccessDialog onContinue={() => router.push("/jobs?submitted=1")} /> : null}
    </div>
  );
}

// ── Multi-part bed editor (2+ STL files) ─────────────────────────────────────

type PartState = { file: File; name: string; transform: PartTransform };
const flatPart: Omit<PartTransform, "x" | "y"> = { scale: 1, rotationX: 0, rotationY: 0, rotationZ: 0 };
const HALF = BED_MM / 2;
const NUDGE = 5;
const NUDGE_FINE = 1;

/** Shelf-pack footprints (sorted by area) into rows, then centre the block on
 * the bed. Returns each part's offset-from-centre position, original order. */
function packLayout(sizes: { x: number; y: number }[], gap = 5): { x: number; y: number }[] {
  const order = sizes.map((_, i) => i).sort((a, b) => sizes[b].x * sizes[b].y - sizes[a].x * sizes[a].y);
  const placed: { cx: number; cy: number }[] = new Array(sizes.length);
  let cursorX = 0;
  let cursorY = 0;
  let rowHeight = 0;
  let blockWidth = 0;
  for (const i of order) {
    const { x: w, y: h } = sizes[i];
    if (cursorX > 0 && cursorX + w > BED_MM) {
      cursorY += rowHeight + gap;
      cursorX = 0;
      rowHeight = 0;
    }
    placed[i] = { cx: cursorX + w / 2, cy: cursorY + h / 2 };
    cursorX += w + gap;
    rowHeight = Math.max(rowHeight, h);
    blockWidth = Math.max(blockWidth, cursorX - gap);
  }
  const offX = blockWidth / 2;
  const offY = (cursorY + rowHeight) / 2;
  return placed.map((p) => ({ x: p.cx - offX, y: p.cy - offY }));
}

function rectsOverlap(
  a: { x: number; y: number; sx: number; sy: number },
  b: { x: number; y: number; sx: number; sy: number },
  eps = 0.5,
) {
  return (
    Math.abs(a.x - b.x) < (a.sx + b.sx) / 2 - eps &&
    Math.abs(a.y - b.y) < (a.sy + b.sy) / 2 - eps
  );
}

function PlateWorkbench({ data, onBack }: { data: PreviewData; onBack: () => void }) {
  const router = useRouter();
  const [parts, setParts] = useState<PartState[]>(() =>
    data.files.map((f) => ({ file: f.file, name: f.name, transform: { x: 0, y: 0, ...flatPart } })),
  );
  const [metrics, setMetrics] = useState<Record<number, PartMetrics>>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [notes, setNotes] = useState("");
  const [printerId, setPrinterId] = useState("");
  const [slotIndex, setSlotIndex] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [objectUrls] = useState(() => data.files.map((f) => URL.createObjectURL(f.file)));
  useEffect(() => () => objectUrls.forEach((url) => URL.revokeObjectURL(url)), [objectUrls]);
  const didArrange = useRef(false);

  const editorParts = useMemo(
    () => parts.map((part, i) => ({ url: objectUrls[i], transform: part.transform })),
    [parts, objectUrls],
  );

  const footprints = useMemo(
    () =>
      parts.map((part, i) => {
        const m = metrics[i];
        return m ? { x: part.transform.x, y: part.transform.y, sx: m.size.x, sy: m.size.y, sz: m.size.z } : null;
      }),
    [parts, metrics],
  );

  const invalid = useMemo(() => {
    const bad = new Set<number>();
    footprints.forEach((f, i) => {
      if (!f) return;
      if (f.x - f.sx / 2 < -HALF || f.x + f.sx / 2 > HALF) bad.add(i);
      if (f.y - f.sy / 2 < -HALF || f.y + f.sy / 2 > HALF) bad.add(i);
      if (f.sz > BED_MM) bad.add(i);
      for (let j = i + 1; j < footprints.length; j += 1) {
        const g = footprints[j];
        if (g && rectsOverlap(f, g)) {
          bad.add(i);
          bad.add(j);
        }
      }
    });
    return bad;
  }, [footprints]);

  const metricsComplete = parts.every((_, i) => metrics[i] != null);
  const canSubmit = parts.length >= 1 && metricsComplete && invalid.size === 0;

  const patchTransform = (i: number, patch: Partial<PartTransform>) =>
    setParts((list) => list.map((p, idx) => (idx === i ? { ...p, transform: { ...p.transform, ...patch } } : p)));

  const onPartMetrics = useCallback((i: number, m: PartMetrics) => {
    setMetrics((prev) => {
      const cur = prev[i];
      if (
        cur &&
        Math.abs(cur.size.x - m.size.x) < 1e-3 &&
        Math.abs(cur.size.y - m.size.y) < 1e-3 &&
        Math.abs(cur.size.z - m.size.z) < 1e-3 &&
        cur.triangles === m.triangles
      ) {
        return prev;
      }
      return { ...prev, [i]: m };
    });
  }, []);

  const autoArrange = useCallback(() => {
    setParts((list) => {
      if (!list.every((_, i) => metrics[i] != null)) return list;
      const pos = packLayout(list.map((_, i) => ({ x: metrics[i].size.x, y: metrics[i].size.y })));
      return list.map((p, i) => ({ ...p, transform: { ...p.transform, x: pos[i].x, y: pos[i].y } }));
    });
  }, [metrics]);

  // One automatic spread once every part has reported its size.
  useEffect(() => {
    if (!didArrange.current && metricsComplete && parts.length > 0) {
      didArrange.current = true;
      autoArrange();
    }
  }, [metricsComplete, parts.length, autoArrange]);

  const removePart = useCallback((i: number) => {
    setParts((list) => list.filter((_, idx) => idx !== i));
    setMetrics((prev) => {
      const next: Record<number, PartMetrics> = {};
      Object.entries(prev).forEach(([k, v]) => {
        const idx = Number(k);
        if (idx < i) next[idx] = v;
        else if (idx > i) next[idx - 1] = v;
      });
      return next;
    });
    setSelected((s) => (s == null ? s : s === i ? null : s > i ? s - 1 : s));
  }, []);

  useEffect(() => {
    if (parts.length === 0) onBack();
  }, [parts.length, onBack]);

  // Delete / Backspace removes, arrows nudge the selected part (5 mm, Shift = 1).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (selected == null) return;
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removePart(selected);
        return;
      }
      const step = event.shiftKey ? NUDGE_FINE : NUDGE;
      let dx = 0;
      let dy = 0;
      if (event.key === "ArrowLeft") dx = -step;
      else if (event.key === "ArrowRight") dx = step;
      else if (event.key === "ArrowUp") dy = step;
      else if (event.key === "ArrowDown") dy = -step;
      else return;
      event.preventDefault();
      const t = parts[selected].transform;
      patchTransform(selected, {
        x: Math.max(-HALF, Math.min(HALF, t.x + dx)),
        y: Math.max(-HALF, Math.min(HALF, t.y + dy)),
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, parts, removePart]);

  const confirm = async () => {
    const sizeError = uploadSizeError(parts.map((part) => part.file));
    if (sizeError) { setError(sizeError); return; }
    setBusy(true);
    setError("");
    const form = new FormData();
    parts.forEach((part) => {
      form.append("files", part.file);
      form.append("scales", String(part.transform.scale));
      form.append("rotations_x", String(part.transform.rotationX));
      form.append("rotations_y", String(part.transform.rotationY));
      form.append("rotations_z", String(part.transform.rotationZ));
      form.append("positions_x", String(part.transform.x));
      form.append("positions_y", String(part.transform.y));
    });
    form.append("user_notes", notes);
    form.append("printer_id", printerId);
    form.append("ams_slot", slotIndex);
    try {
      await api("/api/upload/stl-confirm", { method: "POST", body: form });
      posthog.capture("print_request_submitted", {
        submission_type: "stl",
        file_count: parts.length,
        part_count: parts.length,
        printer_selected: Boolean(printerId),
        filament_preference_selected: Boolean(slotIndex),
      });
      setSubmitted(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "신청하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const sel = selected != null ? parts[selected] : null;
  const selMetrics = selected != null ? metrics[selected] : undefined;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>베드 배치</h1>
          <p>부품을 드래그해 서로 닿지 않게 배치하세요. 방향키로 미세 이동, Delete로 삭제.</p>
        </div>
        <button className="button button-secondary" onClick={onBack}>파일 다시 선택</button>
      </header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <div className="workbench-grid">
        <section className="card workbench-viewer">
          <StlPlateEditor
            parts={editorParts}
            selected={selected}
            invalid={invalid}
            color={selectedFilamentHex(data.printers, printerId, slotIndex)}
            onSelect={setSelected}
            onMove={(i, pos) => patchTransform(i, pos)}
            onPartMetrics={onPartMetrics}
          />
          <p className="viewer-hint">
            {BED_MM} × {BED_MM}mm 베드 · 부품 클릭 후 드래그 · 방향키 {NUDGE}mm (Shift {NUDGE_FINE}mm) · Delete 삭제
          </p>
          <div className="part-list">
            {parts.map((part, index) => (
              <div
                key={objectUrls[index]}
                className={`part-row${selected === index ? " is-selected" : ""}${invalid.has(index) ? " is-invalid" : ""}`}
                onClick={() => setSelected(index)}
              >
                <span className="truncate">{index + 1}. {part.name}</span>
                {metrics[index] ? (
                  <small>{metrics[index].size.x.toFixed(0)}×{metrics[index].size.y.toFixed(0)}×{metrics[index].size.z.toFixed(0)}</small>
                ) : null}
                <button
                  className="icon-button"
                  onClick={(event) => { event.stopPropagation(); removePart(index); }}
                  aria-label={`${part.name} 삭제`}
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
        </section>
        <aside className="card transform-panel">
          <div className="card-header">
            <h2 className="truncate">{sel ? sel.name : `부품 ${parts.length}개`}</h2>
            <button className="button button-secondary button-small" onClick={autoArrange} disabled={!metricsComplete}>
              <LayoutGrid size={14} /> 자동 배치
            </button>
          </div>
          <div className="card-body">
            {invalid.size > 0 ? (
              <div className="notice notice-danger">부품이 겹치거나 베드를 벗어났습니다. 배치를 조정하세요.</div>
            ) : null}
            {sel && selected != null ? (
              <>
                {selMetrics ? (
                  <div className="dimension-row">
                    {(["x", "y", "z"] as const).map((axis) => (
                      <label key={axis}>
                        <span>{axis.toUpperCase()}</span>
                        <input type="text" readOnly value={selMetrics.size[axis].toFixed(1)} />
                        <small>mm</small>
                      </label>
                    ))}
                  </div>
                ) : null}
                <div className="dimension-row">
                  {(["x", "y"] as const).map((axis) => (
                    <label key={axis}>
                      <span>{axis === "x" ? "←→" : "↑↓"}</span>
                      <input
                        type="number"
                        step="1"
                        value={sel.transform[axis].toFixed(0)}
                        onChange={(event) =>
                          patchTransform(selected, {
                            [axis]: Math.max(-HALF, Math.min(HALF, Number(event.target.value) || 0)),
                          })
                        }
                      />
                      <small>mm</small>
                    </label>
                  ))}
                </div>
                <div className="field transform-field">
                  <label htmlFor="part-scale">크기 · {Math.round(sel.transform.scale * 100)}%</label>
                  <input
                    id="part-scale"
                    type="range"
                    min="1"
                    max="400"
                    value={Math.round(sel.transform.scale * 100)}
                    onChange={(event) => patchTransform(selected, { scale: Number(event.target.value) / 100 })}
                  />
                </div>
                <div className="preset-row">
                  {[25, 50, 75, 100].map((percent) => (
                    <button
                      key={percent}
                      className="button button-secondary button-small"
                      onClick={() => patchTransform(selected, { scale: percent / 100 })}
                    >
                      {percent}%
                    </button>
                  ))}
                </div>
                <div className="rotation-grid">
                  {(["rotationX", "rotationY", "rotationZ"] as const).map((axis) => (
                    <div key={axis}>
                      <span>{axis.slice(-1)}</span>
                      <button
                        className="button button-secondary button-small"
                        aria-label={`${axis.slice(-1)}축 반시계 90도`}
                        title="−90°"
                        onClick={() => patchTransform(selected, { [axis]: normalizeAngle(sel.transform[axis] - 90) })}
                      >
                        <RotateCcw size={15} />
                      </button>
                      <AngleDial
                        value={sel.transform[axis]}
                        onChange={(deg) => patchTransform(selected, { [axis]: deg })}
                      />
                      <button
                        className="button button-secondary button-small"
                        aria-label={`${axis.slice(-1)}축 시계 90도`}
                        title="+90°"
                        onClick={() => patchTransform(selected, { [axis]: normalizeAngle(sel.transform[axis] + 90) })}
                      >
                        <RotateCw size={15} />
                      </button>
                    </div>
                  ))}
                </div>
                <button className="button button-secondary button-full button-small" onClick={() => removePart(selected)}>
                  <Trash2 size={14} /> 이 부품 삭제
                </button>
              </>
            ) : (
              <p className="filament-empty">부품을 클릭해 선택하세요</p>
            )}
            <PrinterFilamentPicker
              printers={data.printers}
              printerId={printerId}
              onPrinterChange={setPrinterId}
              slotIndex={slotIndex}
              onSlotChange={setSlotIndex}
            />
            <div className="field">
              <label htmlFor="plate-notes">관리자 메모</label>
              <textarea
                id="plate-notes"
                className="textarea"
                rows={3}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder={"출력물의 목적과 용도, 관리자에게 전달할 내용을 적어주세요.\n목적이 불분명하거나 부적합하다고 판단될 경우 신청이 거부될 수 있습니다."}
              />
            </div>
            <button
              className={`button button-primary button-full ${busy ? "button-loading" : ""}`}
              disabled={busy || !canSubmit}
              onClick={confirm}
            >
              <Check size={16} /> {parts.length}개 부품 한 판 출력 신청
            </button>
          </div>
        </aside>
      </div>
      {submitted ? <SubmissionSuccessDialog onContinue={() => router.push("/jobs?submitted=1")} /> : null}
    </div>
  );
}

export default function UploadPage() {
  const router = useRouter();
  const [stlFiles, setStlFiles] = useState<File[]>([]);
  const [slicedFiles, setSlicedFiles] = useState<File[]>([]);
  const [notes, setNotes] = useState("");
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [printerId, setPrinterId] = useState("");
  const [slotIndex, setSlotIndex] = useState("");
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  useEffect(() => {
    api<{ printers: Printer[] }>("/api/upload").then((data) => setPrinters(data.printers)).catch(() => {});
  }, []);
  // STL preview is entirely client-side — hand the picked files straight to the
  // workbench, no upload. The file only goes to the server on 출력 신청 there.
  const openStlWorkbench = () => {
    setError("");
    const sizeError = uploadSizeError(stlFiles);
    if (sizeError) { setError(sizeError); return; }
    setPreview({ files: stlFiles.map((file) => ({ file, name: file.name })), printers });
  };
  const submitSliced = async () => {
    const sizeError = uploadSizeError(slicedFiles);
    if (sizeError) { setError(sizeError); return; }
    setBusy(true); setError("");
    const form = new FormData();
    slicedFiles.forEach((file) => form.append("files", file));
    form.append("user_notes", notes);
    form.append("printer_id", printerId);
    form.append("ams_slot", slotIndex);
    try {
      await api("/api/upload", { method: "POST", body: form });
      posthog.capture("print_request_submitted", {
        submission_type: "sliced_3mf",
        file_count: slicedFiles.length,
        printer_selected: Boolean(printerId),
        filament_preference_selected: Boolean(slotIndex),
      });
      setSubmitted(true);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "업로드하지 못했습니다."); } finally { setBusy(false); }
  };
  if (preview) {
    return preview.files.length >= 2 ? (
      <PlateWorkbench data={preview} onBack={() => setPreview(null)} />
    ) : (
      <StlWorkbench data={preview} onBack={() => setPreview(null)} />
    );
  }
  return (
    <div className="page">
      <header className="page-header"><div><h1>출력 신청</h1><p>STL 파일을 바로 올리면 사이트가 자동으로 슬라이싱합니다. 이미 슬라이싱한 파일은 오른쪽에 제출하세요.</p></div></header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <div className="upload-grid">
        <section className="card upload-path">
          <div className="upload-path-title"><Box size={22} /><div><h2>STL 파일</h2><p>브라우저에서 크기와 회전을 확인한 뒤 서버에서 슬라이싱합니다.</p></div></div>
          <FilePicker id="stl-files" acceptAttribute=".stl" accept={(file) => file.name.toLowerCase().endsWith(".stl")} files={stlFiles} onFiles={setStlFiles} title="STL 파일 선택 또는 드롭" hint="최대 100MB" />
          <div className="notice upload-guidance">서포트가 필요하거나 복잡한 모델은 Bambu Studio로 슬라이싱하세요.</div>
          <button className="button button-primary button-full" disabled={!stlFiles.length || busy} onClick={openStlWorkbench}><Box size={16} /> 3D 미리보기</button>
        </section>
        <section className="card upload-path">
          <div className="upload-path-title"><FileArchive size={22} /><div><h2>슬라이싱된 3MF</h2><p>Bambu Studio에서 내보낸 .gcode.3mf 파일을 그대로 제출합니다.</p></div></div>
          <FilePicker id="sliced-files" acceptAttribute=".gcode.3mf" accept={(file) => file.name.toLowerCase().endsWith(".gcode.3mf")} files={slicedFiles} onFiles={setSlicedFiles} title=".gcode.3mf 파일 선택 또는 드롭" hint="최대 100MB" />
          <PrinterFilamentPicker printers={printers} printerId={printerId} onPrinterChange={setPrinterId} slotIndex={slotIndex} onSlotChange={setSlotIndex} />
          <div className="field"><label htmlFor="sliced-notes">관리자 메모</label><textarea id="sliced-notes" className="textarea" rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder={"출력물의 목적과 용도, 관리자에게 전달할 내용을 적어주세요.\n목적이 불분명하거나 부적합하다고 판단될 경우 신청이 거부될 수 있습니다."} /></div>
          <button className={`button button-primary button-full ${busy ? "button-loading" : ""}`} disabled={!slicedFiles.length || busy} onClick={submitSliced}><Check size={16} /> 출력 신청</button>
        </section>
      </div>
      {submitted ? <SubmissionSuccessDialog onContinue={() => router.push("/jobs?submitted=1")} /> : null}
    </div>
  );
}
