"use client";

import { Box, Check, CheckCircle2, FileArchive, FileUp, RotateCcw, RotateCw, X } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import posthog from "posthog-js";

import { StlViewer, type ModelTransform } from "@/components/stl-viewer";
import { api, formatBytes } from "@/lib/api";
import type { Printer } from "@/lib/types";

type PreviewFile = { file: File; name: string };
type PreviewData = { files: PreviewFile[]; printers: Printer[] };
type Dimensions = { x: number; y: number; z: number };
const initialTransform: ModelTransform = { scale: 1, rotationX: 0, rotationY: 0, rotationZ: 0 };
const MAX_FILE_BYTES = 100 * 1024 * 1024;

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
          </div>
        </div>
        <button ref={continueRef} className="button button-primary button-full" onClick={onContinue}>내 작업에서 확인</button>
      </div>
    </dialog>
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
            <div className="rotation-grid">{(["rotationX", "rotationY", "rotationZ"] as const).map((axis) => <div key={axis}><span>{axis.slice(-1)}</span><button className="button button-secondary button-small" aria-label={`${axis.slice(-1)}축 반시계 방향 90도 회전`} title="−90°" onClick={() => setCurrent({ [axis]: current[axis] - 90 })}><RotateCcw size={15} /></button><strong>{current[axis]}°</strong><button className="button button-secondary button-small" aria-label={`${axis.slice(-1)}축 시계 방향 90도 회전`} title="+90°" onClick={() => setCurrent({ [axis]: current[axis] + 90 })}><RotateCw size={15} /></button></div>)}</div>
            <PrinterFilamentPicker printers={data.printers} printerId={printerId} onPrinterChange={setPrinterId} slotIndex={slotIndex} onSlotChange={setSlotIndex} />
            <div className="field"><label htmlFor="notes">관리자 메모</label><textarea id="notes" className="textarea" rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="출력 시 참고할 내용 (선택)" /></div>
            <button className={`button button-primary button-full ${busy ? "button-loading" : ""}`} disabled={busy || tooLarge} onClick={confirm}><Check size={16} /> {data.files.length}개 파일 출력 신청</button>
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
    const oversize = stlFiles.find((file) => file.size > MAX_FILE_BYTES);
    if (oversize) { setError(`${oversize.name} 파일이 100MB를 넘습니다`); return; }
    setPreview({ files: stlFiles.map((file) => ({ file, name: file.name })), printers });
  };
  const submitSliced = async () => {
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
  if (preview) return <StlWorkbench data={preview} onBack={() => setPreview(null)} />;
  return (
    <div className="page">
      <header className="page-header"><div><h1>출력 신청</h1><p>STL을 바로 슬라이싱하거나 Bambu Studio에서 준비한 파일을 제출할 수 있습니다.</p></div></header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <div className="upload-grid">
        <section className="card upload-path">
          <div className="upload-path-title"><Box size={22} /><div><h2>STL 파일</h2><p>브라우저에서 크기와 회전을 확인한 뒤 서버에서 슬라이싱합니다.</p></div></div>
          <FilePicker id="stl-files" acceptAttribute=".stl" accept={(file) => file.name.toLowerCase().endsWith(".stl")} files={stlFiles} onFiles={setStlFiles} title="STL 파일 선택 또는 드롭" hint="여러 파일 · 각 파일 최대 100MB" />
          <div className="notice upload-guidance">복잡한 모델은 Bambu Studio로 직접 슬라이싱하면 더 정확하게 설정할 수 있습니다.</div>
          <button className="button button-primary button-full" disabled={!stlFiles.length || busy} onClick={openStlWorkbench}><Box size={16} /> 3D 미리보기</button>
        </section>
        <section className="card upload-path">
          <div className="upload-path-title"><FileArchive size={22} /><div><h2>슬라이싱된 3MF</h2><p>Bambu Studio에서 내보낸 .gcode.3mf 파일을 그대로 제출합니다.</p></div></div>
          <FilePicker id="sliced-files" acceptAttribute=".gcode.3mf" accept={(file) => file.name.toLowerCase().endsWith(".gcode.3mf")} files={slicedFiles} onFiles={setSlicedFiles} title=".gcode.3mf 파일 선택 또는 드롭" hint="여러 파일 · 각 파일 최대 100MB" />
          <PrinterFilamentPicker printers={printers} printerId={printerId} onPrinterChange={setPrinterId} slotIndex={slotIndex} onSlotChange={setSlotIndex} />
          <div className="field"><label htmlFor="sliced-notes">관리자 메모</label><textarea id="sliced-notes" className="textarea" rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="출력 시 참고할 내용 (선택)" /></div>
          <button className={`button button-primary button-full ${busy ? "button-loading" : ""}`} disabled={!slicedFiles.length || busy} onClick={submitSliced}><Check size={16} /> 출력 신청</button>
        </section>
      </div>
      {submitted ? <SubmissionSuccessDialog onContinue={() => router.push("/jobs?submitted=1")} /> : null}
    </div>
  );
}
