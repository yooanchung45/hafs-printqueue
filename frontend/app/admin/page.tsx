"use client";
import Link from "next/link";
import { AlertTriangle, BarChart3, Check, ChevronDown, ChevronUp, Download, Lightbulb, MoreHorizontal, Plus, RefreshCw, RotateCcw, Settings, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { JobPreview } from "@/components/job-preview";
import { StatusBadge } from "@/components/status-badge";
import { api, ApiError, formatBytes, formatDate } from "@/lib/api";
import type { Job, Printer, User } from "@/lib/types";

type AdminData = { user: User; pending_jobs: Job[]; printers: Printer[] };
type Transfer = { phase: string; message: string; progress: number | null; bytes_sent?: number; bytes_total?: number };

function formData(values: Record<string, string | number | null | undefined>) {
  const form = new FormData();
  Object.entries(values).forEach(([key, value]) => value != null && form.append(key, String(value)));
  return form;
}

function JobName({ job }: { job: Job }) {
  // admin_notes on a still-pending job only ever comes from a failed slice
  // (rejections/print failures move the job to another status).
  const sliceFailed = job.status === "pending_approval" && !!job.admin_notes;
  return <div className="admin-job-name"><strong className="truncate">{job.filename}</strong><span>{job.owner?.name ?? "—"} · {formatBytes(job.file_size)}{sliceFailed ? <span className="slice-failed-tag"> · 슬라이싱 실패</span> : null}</span></div>;
}

type Mutate = (key: string, path: string, values?: Record<string, string | number | null | undefined>, confirmation?: string) => Promise<void>;

export default function AdminPage() {
  const [data, setData] = useState<AdminData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [transfer, setTransfer] = useState<Transfer | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const load = useCallback(async () => {
    try { setData(await api<AdminData>("/api/admin")); setError(""); }
    catch (caught) { if (caught instanceof ApiError && caught.status !== 401) setError(caught.message); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    if (!data?.printers.some((printer) => printer.jobs?.some((job) => job.status === "printing"))) return;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [data, load]);

  const mutate = async (key: string, path: string, values?: Record<string, string | number | null | undefined>, confirmation?: string) => {
    if (confirmation && !window.confirm(confirmation)) return;
    setBusy(key); setError("");
    try { await api(path, { method: "POST", body: values ? formData(values) : undefined }); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "작업을 처리하지 못했습니다."); }
    finally { setBusy(""); }
  };

  const startTransfer = async (job: Job, slot: string) => {
    setBusy(`start-${job.id}`); setError("");
    try {
      const printer = data?.printers.find((item) => item.id === job.printer_id);
      if (!printer?.ip || !printer.has_access_code || !printer.serial) {
        await api(`/api/admin/jobs/${job.id}/start`, {
          method: "POST",
          body: formData({ ams_slot: slot }),
        });
        setBusy("");
        await load();
        return;
      }
      const result = await api<{ transfer_id: string }>(`/api/admin/jobs/${job.id}/start-transfer`, { method: "POST", body: formData({ ams_slot: slot }) });
      setTransfer({ phase: "preparing", message: "파일 전송 준비 중", progress: 0 });
      const poll = window.setInterval(async () => {
        try {
          const state = await api<Transfer>(`/api/admin/transfers/${result.transfer_id}`);
          setTransfer(state);
          if (["done", "error"].includes(state.phase)) { window.clearInterval(poll); setBusy(""); await load(); }
        } catch { window.clearInterval(poll); setBusy(""); }
      }, 500);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "전송을 시작하지 못했습니다."); setBusy(""); }
  };

  if (!data) return <div className="page">{error ? <div className="notice notice-danger">{error}</div> : <div className="skeleton" />}</div>;
  return (
    <div className="page admin-page">
      <header className="page-header"><div><h1>관리자 운영</h1></div><div className="page-actions"><Link href="/admin/reports" className="button button-secondary"><BarChart3 size={16} /> 출력 일지</Link><button className="button button-secondary" onClick={() => setSettingsOpen(true)}><Settings size={16} /> 프린터 설정</button><button className={`button button-primary ${busy === "sync" ? "button-loading" : ""}`} disabled={!!busy} onClick={() => mutate("sync", "/api/admin/printers/sync")}><RefreshCw size={16} /> 상태 동기화</button></div></header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <section className="admin-section admin-approval-section"><div className="section-heading"><div><h2>승인 대기</h2><p>파일과 메모를 확인한 뒤 대기열에 넣습니다.</p></div><span className="section-count">{data.pending_jobs.length}</span></div>
        {data.pending_jobs.length ? <div className="card table-wrap admin-approval-scroll"><table className="table"><thead><tr><th>작업</th><th>신청</th><th>프린터</th><th>메모</th><th>처리</th></tr></thead><tbody>{data.pending_jobs.map((job) => <tr key={job.id}><td><JobName job={job} /></td><td className="muted">{formatDate(job.created_at)}</td><td><select className="select compact-select" defaultValue={job.printer_id} onChange={(event) => mutate(`assign-${job.id}`, `/api/admin/jobs/${job.id}/reassign`, { printer_id: event.target.value })}>{data.printers.map((printer) => <option value={printer.id} key={printer.id}>{printer.name}</option>)}</select></td><td className="admin-note">{job.admin_notes ? <span className="admin-note-alert">⚠ {job.admin_notes}</span> : null}{job.user_notes ? <span className="muted">{job.user_notes}</span> : null}{!job.admin_notes && !job.user_notes ? <span className="muted">—</span> : null}</td><td><div className="table-actions"><button className="button button-primary button-small" disabled={!!busy} onClick={() => mutate(`approve-${job.id}`, `/api/admin/jobs/${job.id}/approve`)}>승인</button><button className="button button-danger button-small" disabled={!!busy} onClick={() => { const reason = window.prompt("거절 사유를 입력하세요."); if (reason !== null) void mutate(`reject-${job.id}`, `/api/admin/jobs/${job.id}/reject`, { reason }); }}>거절</button><JobPreview job={job} admin /><a className="icon-button" href={`/api/admin/jobs/${job.id}/download`} aria-label="파일 다운로드"><Download size={16} /></a></div></td></tr>)}</tbody></table></div> : <div className="admin-empty">승인을 기다리는 작업이 없습니다.</div>}
      </section>

      <section className="admin-section"><div className="section-heading"><div><h2>프린터와 대기열</h2><p>프린터별 작업을 이동하고 출력을 관리합니다.</p></div></div><div className="admin-printer-grid">{data.printers.map((printer) => <AdminPrinter key={printer.id} printer={printer} printers={data.printers} busy={busy} mutate={mutate} startTransfer={startTransfer} />)}</div></section>

      <PrinterSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} printers={data.printers} mutate={mutate} busy={busy} />
      {transfer ? <div className="transfer-toast" role="status"><div><strong>{transfer.phase === "error" ? "전송 실패" : "프린터 전송"}</strong><span>{transfer.message}</span></div>{transfer.progress != null ? <div className="progress"><span style={{ width: `${transfer.progress}%` }} /></div> : null}{["done", "error"].includes(transfer.phase) ? <button className="icon-button" onClick={() => setTransfer(null)} aria-label="닫기"><X size={16} /></button> : null}</div> : null}
    </div>
  );
}

function MoveSelect({ job, printer, printers, mutate, retry = false }: { job: Job; printer: Printer; printers: Printer[]; mutate: Mutate; retry?: boolean }) {
  const destinations = printers.filter((item) => item.id !== printer.id);
  if (!destinations.length) return null;
  return <select
    className="select compact-select job-move-select"
    aria-label={`${job.filename} 프린터 이동`}
    value=""
    onChange={(event) => {
      const printerId = event.target.value;
      if (!printerId) return;
      void mutate(
        `${retry ? "retry" : "assign"}-${job.id}-${printerId}`,
        retry ? `/api/admin/jobs/${job.id}/retry` : `/api/admin/jobs/${job.id}/reassign`,
        { printer_id: printerId, ...(retry ? { at_front: 0 } : {}) },
      );
    }}
  >
    <option value="">프린터 이동</option>
    {destinations.map((item) => <option value={item.id} key={item.id}>{item.name} · {(item.jobs ?? []).filter((queued) => queued.status === "queued").length}개 대기</option>)}
  </select>;
}

function JobMore({ job, printer, printers, mutate }: { job: Job; printer: Printer; printers: Printer[]; mutate: Mutate }) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });

  useEffect(() => {
    if (!open) return;
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    const closeOnViewportChange = () => setOpen(false);
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [open]);

  const toggle = () => {
    if (open) { setOpen(false); return; }
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const menuWidth = 224;
    setPosition({
      left: Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8)),
      top: Math.min(rect.bottom + 6, window.innerHeight - 176),
    });
    setOpen(true);
  };

  return <>
    <button ref={triggerRef} className="icon-button" aria-label={`${job.filename} 옵션`} aria-haspopup="dialog" aria-expanded={open} onClick={toggle}><MoreHorizontal size={16} /></button>
    {open ? createPortal(<div ref={menuRef} className="job-context-menu" role="dialog" aria-label={`${job.filename} 작업 옵션`} style={position}>
      <MoveSelect job={job} printer={printer} printers={printers} mutate={mutate} />
      <div className="job-context-row"><JobPreview job={job} admin /><button className="button button-danger button-small" onClick={() => { setOpen(false); void mutate(`delete-${job.id}`, `/api/admin/jobs/${job.id}/delete-queued`, undefined, "대기열과 저장 파일에서 완전히 삭제할까요?"); }}>삭제</button></div>
    </div>, document.body) : null}
  </>;
}

function FailedJob({ job, printer, printers, mutate, busy }: { job: Job; printer: Printer; printers: Printer[]; mutate: Mutate; busy: string }) {
  return <div className="admin-failure-item"><AlertTriangle size={16} aria-hidden="true" /><div className="admin-failure-copy"><JobName job={job} /><span>{job.admin_notes || "출력 실패를 확인한 뒤 조치해 주세요."}</span></div><div className="admin-failure-actions"><button className="button button-secondary button-small" disabled={!!busy} onClick={() => mutate(`retry-${job.id}`, `/api/admin/jobs/${job.id}/retry`, { at_front: 1 })}><RotateCcw size={14} /> 같은 프린터 재시도</button><MoveSelect job={job} printer={printer} printers={printers} mutate={mutate} retry /><button className="button button-ghost button-small" disabled={!!busy} onClick={() => mutate(`dismiss-${job.id}`, `/api/admin/jobs/${job.id}/dismiss-failure`)}><Check size={14} /> 확인 처리</button></div></div>;
}

function AdminPrinter({ printer, printers, busy, mutate, startTransfer }: { printer: Printer; printers: Printer[]; busy: string; mutate: Mutate; startTransfer: (job: Job, slot: string) => Promise<void> }) {
  const [slot, setSlot] = useState("");
  const jobs = printer.jobs ?? [];
  const loadedSlots = printer.slots?.filter((item) => !item.is_empty) ?? [];
  const selectedSlotAvailable = loadedSlots.some((item) => String(item.slot_index) === slot);
  const firstQueuedJob = jobs.find((job) => job.status === "queued");
  const firstQueuedId = firstQueuedJob?.id;
  const requestedSlot = firstQueuedJob?.ams_slot != null
    ? printer.slots?.find((item) => item.slot_index === firstQueuedJob.ams_slot)
    : null;

  // Pre-fill from the student's requested color when a new job reaches the
  // front of the queue, but only then — re-running on every printer poll
  // (every 5s) would stomp on an admin actively changing the dropdown for
  // the job currently at the front.
  useEffect(() => {
    const requested = firstQueuedJob?.ams_slot;
    if (requested != null && loadedSlots.some((item) => item.slot_index === requested)) {
      setSlot(String(requested));
    } else {
      setSlot("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstQueuedId]);

  const progressSuffix = ["printing", "paused"].includes(printer.status) && printer.progress != null ? `${printer.progress}%` : undefined;
  return <article className="card admin-printer"><header className="admin-printer-header"><div><h3>{printer.name}</h3><span>{printer.nozzle_temp != null ? `노즐 ${Math.round(printer.nozzle_temp)}° · 베드 ${Math.round(printer.bed_temp ?? 0)}°` : "온도 정보 없음"}</span></div><StatusBadge status={printer.status} suffix={progressSuffix} /></header>
    {(printer.failed_jobs ?? []).length ? <section className="admin-failures" aria-label={`${printer.name} 실패 작업`}><div className="admin-failure-heading"><AlertTriangle size={15} /><strong>조치가 필요한 실패 작업</strong><span>{printer.failed_jobs!.length}</span></div>{printer.failed_jobs!.map((job) => <FailedJob key={job.id} job={job} printer={printer} printers={printers} mutate={mutate} busy={busy} />)}</section> : null}
    <div className="admin-queue-scroll"><div className="admin-queue">{jobs.length ? jobs.map((job) => <div className="admin-queue-item" key={job.id}><div className="admin-queue-index">{job.status === "queued" ? job.queue_position : "•"}</div><JobName job={job} /><div className="admin-job-top-actions">{job.status === "queued" ? <><button className="icon-button" aria-label={`${job.filename} 위로`} onClick={() => mutate(`up-${job.id}`, `/api/admin/jobs/${job.id}/move`, { direction: "up" })}><ChevronUp size={16} /></button><button className="icon-button" aria-label={`${job.filename} 아래로`} onClick={() => mutate(`down-${job.id}`, `/api/admin/jobs/${job.id}/move`, { direction: "down" })}><ChevronDown size={16} /></button><JobMore job={job} printer={printer} printers={printers} mutate={mutate} /></> : <StatusBadge status={job.status} />}</div><div className="admin-queue-actions">{job.status === "queued" && job.id === firstQueuedId ? <><button className={`button button-primary button-small ${busy === `start-${job.id}` ? "button-loading" : ""}`} disabled={!!busy || !selectedSlotAvailable || ["printing", "paused"].includes(printer.status)} onClick={() => startTransfer(job, slot)}>출력 시작</button><select className="select compact-select filament-select" aria-label="출력할 필라멘트 색상" value={selectedSlotAvailable ? slot : ""} onChange={(event) => setSlot(event.target.value)} required><option value="" disabled>색상 선택</option>{loadedSlots.map((item) => <option key={item.id} value={item.slot_index}>{item.color_name ?? "색상 미확인"} · {item.material_type ?? "재질 미확인"} (슬롯 {item.slot_index + 1})</option>)}</select>{job.ams_slot != null ? <span className="admin-note filament-request-note">학생 요청: {requestedSlot ? `${requestedSlot.color_name ?? "색상 미확인"} (슬롯 ${requestedSlot.slot_index + 1})` : `슬롯 ${job.ams_slot + 1} (현재 없음)`}</span> : null}</> : null}{job.status === "printing" ? <button className="button button-danger button-small" onClick={() => mutate(`cancel-${job.id}`, `/api/admin/jobs/${job.id}/cancel`, undefined, "프린터 출력을 즉시 취소할까요?")}>출력 취소</button> : null}{job.status === "awaiting_clear" ? <><button className="button button-primary button-small" onClick={() => mutate(`complete-${job.id}`, `/api/admin/jobs/${job.id}/complete`)}>베드 비움 완료</button><JobPreview job={job} admin /></> : null}</div></div>) : <div className="admin-empty">대기 중인 작업이 없습니다.</div>}</div></div>
  </article>;
}

function PrinterSettingsDialog({ open, onClose, printers, mutate, busy }: { open: boolean; onClose: () => void; printers: Printer[]; mutate: Mutate; busy: string }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [add, setAdd] = useState({ name: "", serial: "", ip: "", access_code: "" });
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      window.requestAnimationFrame(() => dialog.querySelector<HTMLInputElement>("input")?.focus());
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return <dialog ref={dialogRef} className="modal printer-settings-dialog" onClose={onClose} onCancel={onClose} onClick={(event) => event.target === event.currentTarget && onClose()}><header className="modal-header"><div><h2>프린터 설정</h2><p>연결 정보와 조명, 출력을 관리합니다.</p></div><button className="icon-button" onClick={onClose} aria-label="프린터 설정 닫기"><X size={18} /></button></header><div className="modal-body"><div className="settings-grid">{printers.map((printer) => {
    const canStop = ["printing", "paused"].includes(printer.status) || (printer.jobs ?? []).some((job) => job.status === "printing");
    return <form className="card card-body printer-form" key={printer.id} onSubmit={(event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>; void mutate(`edit-${printer.id}`, `/api/admin/printers/${printer.id}/edit`, values); }}><h3>{printer.name}</h3><input className="input" name="name" defaultValue={printer.name} aria-label="이름" required /><input className="input mono" name="serial" defaultValue={printer.serial ?? ""} aria-label="시리얼" required /><input className="input mono" name="ip" defaultValue={printer.ip ?? ""} aria-label="IP 주소" required /><input className="input mono" name="access_code" defaultValue={printer.access_code ?? ""} aria-label="액세스 코드" required /><div className="form-actions"><button className="button button-secondary button-small" disabled={!!busy}>저장</button><button type="button" className="button button-ghost button-small" disabled={!!busy} onClick={() => mutate(`light-${printer.id}`, `/api/admin/printers/${printer.id}/light`, { on: 1 })}><Lightbulb size={14} /> 켜기</button><button type="button" className="button button-ghost button-small" disabled={!!busy} onClick={() => mutate(`light-off-${printer.id}`, `/api/admin/printers/${printer.id}/light`, { on: 0 })}>끄기</button><button type="button" className={`button button-danger button-small ${busy === `stop-printer-${printer.id}` ? "button-loading" : ""}`} disabled={!!busy || !canStop} onClick={() => mutate(`stop-printer-${printer.id}`, `/api/admin/printers/${printer.id}/stop`, undefined, `${printer.name}의 출력을 즉시 중단할까요? 진행 중인 작업은 취소 처리됩니다.`)}><Square size={13} /> 출력 중단</button><button type="button" className="button button-danger button-small" disabled={!!busy} onClick={() => mutate(`delete-printer-${printer.id}`, `/api/admin/printers/${printer.id}/delete`, undefined, "출력 기록이 없는 경우에만 삭제됩니다. 계속할까요?")}>삭제</button></div></form>;
  })}<form className="card card-body printer-form add-printer" onSubmit={(event) => { event.preventDefault(); void mutate("add-printer", "/api/admin/printers/add", add).then(() => setAdd({ name: "", serial: "", ip: "", access_code: "" })); }}><h3><Plus size={16} /> 프린터 추가</h3>{Object.entries(add).map(([key, value]) => <input key={key} className="input mono" required value={value} placeholder={{ name: "표시 이름", serial: "시리얼", ip: "IP 주소", access_code: "액세스 코드" }[key]} onChange={(event) => setAdd((current) => ({ ...current, [key]: event.target.value }))} />)}<button className="button button-primary button-small" disabled={!!busy}>추가</button></form></div></div></dialog>;
}
