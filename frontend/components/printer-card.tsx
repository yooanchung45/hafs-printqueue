import { Layers3, Thermometer } from "lucide-react";

import { CameraFeed } from "@/components/camera-feed";
import { JobPreview } from "@/components/job-preview";
import { StatusBadge } from "@/components/status-badge";
import type { Printer } from "@/lib/types";

export function PrinterCard({ printer, userId }: { printer: Printer; userId: number }) {
  const jobs = printer.jobs ?? [];
  return (
    <article className="card printer-card">
      <header className="printer-card-header">
        <div>
          <h2>{printer.name}</h2>
          {printer.nozzle_temp != null ? (
            <span className="temperature"><Thermometer size={13} /> 노즐 {Math.round(printer.nozzle_temp)}° · 베드 {Math.round(printer.bed_temp ?? 0)}°</span>
          ) : <span className="temperature">상태 정보 없음</span>}
        </div>
        <StatusBadge status={printer.status} suffix={printer.status === "printing" && printer.progress != null ? `${printer.progress}%` : undefined} />
      </header>
      {printer.status !== "offline" && printer.ip && printer.has_access_code ? (
        <CameraFeed printerId={printer.id} printerName={printer.name} />
      ) : null}
      {(printer.slots?.length ?? 0) > 0 ? (
        <div className="filament-row" aria-label="필라멘트 슬롯">
          {printer.slots!.map((slot) => (
            <div className="filament-slot" key={slot.id} title={`슬롯 ${slot.slot_index + 1}: ${slot.is_empty ? "비어 있음" : `${slot.material_type ?? "필라멘트"} ${slot.color_name ?? ""}`}`}>
              <span className="filament-swatch" style={{ "--filament": slot.color_hex ?? "var(--color-paper-3)" } as React.CSSProperties} />
              <span>{slot.is_empty ? "빈 슬롯" : slot.material_type ?? "—"}</span>
            </div>
          ))}
        </div>
      ) : null}
      <div className="queue-heading"><span><Layers3 size={14} /> 현재 대기열</span><strong>{jobs.length}</strong></div>
      {jobs.length ? (
        <ol className="queue-list">
          {jobs.map((job) => (
            <li key={job.id} className={job.user_id === userId ? "queue-item queue-item-mine" : "queue-item"}>
              <div className="queue-position">{job.status === "queued" ? job.queue_position ?? "—" : <span className="queue-live" />}</div>
              <div className="queue-copy">
                <strong className="truncate">{job.filename}</strong>
                <span>{job.user_id === userId ? "내 작업" : job.status === "awaiting_clear" ? "출력 완료" : job.status === "printing" ? "출력 중" : "대기 중"}</span>
              </div>
              <JobPreview job={job} />
            </li>
          ))}
        </ol>
      ) : <p className="printer-empty">현재 대기 중인 작업이 없습니다.</p>}
    </article>
  );
}
