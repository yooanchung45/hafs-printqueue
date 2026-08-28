"use client";

import { Box, Check, CheckCircle2, FileArchive, FileUp, RotateCcw, RotateCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { StlViewer, type ModelTransform } from "@/components/stl-viewer";
import { api, formatBytes } from "@/lib/api";
import type { Printer } from "@/lib/types";

type TempFile = { temp_id: string; original_name: string; url: string };
type PreviewData = { files: TempFile[]; printers: Printer[] };
type Dimensions = { x: number; y: number; z: number };
const initialTransform: ModelTransform = { scale: 1, rotationX: 0, rotationY: 0, rotationZ: 0 };

// Shared by both submission paths (direct .gcode.3mf upload and the STL
// confirm step). Printer choice is optional (blank = the backend's
// pick_best_printer picks whichever healthy printer has the shortest
// queue). The filament dropdown is always shown for consistency, but only
// becomes selectable once a specific printer is picked — slot layouts and
// colors differ per printer, and under ✨ 스마트 배정 we don't yet know
// which printer the job will land on.
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
  const selected = printers.find((printer) => String(printer.id) === printerId);
  const loadedSlots = selected?.slots?.filter((item) => !item.is_empty) ?? [];
  const noPrinter = !selected;
  const noFilament = !noPrinter && loadedSlots.length === 0;
  const disabled = noPrinter || noFilament;
  const currentSwatch = loadedSlots.find((item) => String(item.slot_index) === slotIndex)?.color_hex ?? null;
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
        <label htmlFor="filament-choice">필라멘트 색상</label>
        <div className="filament-field">
          {currentSwatch ? (
            <span
              className="filament-field-swatch"
              style={{ "--filament": currentSwatch } as React.CSSProperties}
              aria-hidden="true"
            />
          ) : null}
          <select
            id="filament-choice"
            className="select"
            value={disabled ? "" : slotIndex}
            disabled={disabled}
            onChange={(event) => onSlotChange(event.target.value)}
          >
            {noPrinter ? (
              <option value="">프린터가 선택되지 않음</option>
            ) : noFilament ? (
              <option value="">로드된 필라멘트가 없음</option>
            ) : (
              <>
                <option value="">색상 선호 없음</option>
                {loadedSlots.map((item) => (
                  <option
                    key={item.id}
                    value={item.slot_index}
                    style={item.color_hex ? { color: item.color_hex } : undefined}
                  >
                    ● {item.color_name ?? "색상 미확인"} · {item.material_type ?? "재질 미확인"} (슬롯 {item.slot_index + 1})
                  </option>
                ))}
              </>
            )}
          </select>
        </div>
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
  const [notes, setNotes] = useState("");
  const [printerId, setPrinterId] = useState("");
  const [slotIndex, setSlotIndex] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
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
    data.files.forEach((file, index) => {
      form.append("file_ids", file.temp_id); form.append("filenames", file.original_name);
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
      setSubmitted(true);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "신청하지 못했습니다."); } finally { setBusy(false); }
  };
  return (
    <div className="page">
      <header className="page-header"><div><h1>STL 크기와 방향 확인</h1><p>출력 베드 안에 들어오는지 확인한 뒤 제출하세요.</p></div><button className="button button-secondary" onClick={onBack}>파일 다시 선택</button></header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <div className="workbench-grid">
        <section className="card workbench-viewer">
          <StlViewer url={data.files[selected].url} transform={current} onDimensions={useCallback((next: Dimensions) => setDimensions((values) => ({ ...values, [selected]: next })), [selected])} />
          <p className="viewer-hint">256 × 256mm 베드 · 드래그로 회전 · 스크롤로 확대</p>
          <div className="model-tabs">{data.files.map((file, index) => <button key={file.temp_id} className={selected === index ? "model-tab model-tab-active" : "model-tab"} onClick={() => setSelected(index)}>{index + 1}. {file.original_name}</button>)}</div>
        </section>
        <aside className="card transform-panel">
          <div className="card-header"><h2 className="truncate">{data.files[selected].original_name}</h2><button className="icon-button" onClick={() => setCurrent(initialTransform)} title="변형 초기화"><RotateCcw size={16} /></button></div>
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
  const [busy, setBusy] = useState<"stl" | "sliced" | null>(null);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  useEffect(() => {
    api<{ printers: Printer[] }>("/api/upload").then((data) => setPrinters(data.printers)).catch(() => {});
  }, []);
  const submit = async (kind: "stl" | "sliced") => {
    setBusy(kind); setError("");
    const form = new FormData();
    const files = kind === "stl" ? stlFiles : slicedFiles;
    files.forEach((file) => form.append("files", file));
    if (kind === "sliced") {
      form.append("user_notes", notes);
      form.append("printer_id", printerId);
      form.append("ams_slot", slotIndex);
    }
    try {
      if (kind === "stl") setPreview(await api<PreviewData>("/api/upload/stl-preview", { method: "POST", body: form }));
      else { await api("/api/upload", { method: "POST", body: form }); setSubmitted(true); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "업로드하지 못했습니다."); } finally { setBusy(null); }
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
          <button className={`button button-primary button-full ${busy === "stl" ? "button-loading" : ""}`} disabled={!stlFiles.length || busy !== null} onClick={() => submit("stl")}><Box size={16} /> 3D 미리보기</button>
        </section>
        <section className="card upload-path">
          <div className="upload-path-title"><FileArchive size={22} /><div><h2>슬라이싱된 3MF</h2><p>Bambu Studio에서 내보낸 .gcode.3mf 파일을 그대로 제출합니다.</p></div></div>
          <FilePicker id="sliced-files" acceptAttribute=".gcode.3mf" accept={(file) => file.name.toLowerCase().endsWith(".gcode.3mf")} files={slicedFiles} onFiles={setSlicedFiles} title=".gcode.3mf 파일 선택 또는 드롭" hint="여러 파일 · 각 파일 최대 100MB" />
          <PrinterFilamentPicker printers={printers} printerId={printerId} onPrinterChange={setPrinterId} slotIndex={slotIndex} onSlotChange={setSlotIndex} />
          <div className="field"><label htmlFor="sliced-notes">관리자 메모</label><textarea id="sliced-notes" className="textarea" rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="출력 시 참고할 내용 (선택)" /></div>
          <button className={`button button-primary button-full ${busy === "sliced" ? "button-loading" : ""}`} disabled={!slicedFiles.length || busy !== null} onClick={() => submit("sliced")}><Check size={16} /> 출력 신청</button>
        </section>
      </div>
      {submitted ? <SubmissionSuccessDialog onContinue={() => router.push("/jobs?submitted=1")} /> : null}
    </div>
  );
}
