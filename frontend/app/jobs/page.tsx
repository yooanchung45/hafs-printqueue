"use client";

import Link from "next/link";
import { ClipboardList, RotateCw, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import posthog from "posthog-js";

import { EmptyState } from "@/components/empty-state";
import { JobPreview } from "@/components/job-preview";
import { StatusBadge } from "@/components/status-badge";
import { api, ApiError, formatBytes, formatDate, printProgress } from "@/lib/api";
import type { Job } from "@/lib/types";

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api<{ jobs: Job[] }>("/api/jobs");
      setJobs(data.jobs);
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status !== 401) setError(caught.message);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    if (!jobs?.some((job) => job.status === "processing" || job.status === "printing")) return;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [jobs, load]);

  const cancel = async (job: Job) => {
    if (!window.confirm(`“${job.filename}” 작업을 취소할까요?`)) return;
    setBusy(job.id);
    try {
      await api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
      posthog.capture("print_request_cancelled", { job_status: job.status });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "취소하지 못했습니다.");
    } finally { setBusy(null); }
  };

  return (
    <div className="page">
      <header className="page-header">
        <div><h1>내 작업</h1></div>
        <div className="page-actions">
          <button className="button button-ghost" onClick={load}><RotateCw size={16} /> 새로고침</button>
          <Link href="/upload" className="button button-primary"><Upload size={16} /> 새 출력 신청</Link>
        </div>
      </header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      {jobs === null ? <div className="skeleton" /> : jobs.length === 0 ? (
        <EmptyState icon={ClipboardList} title="아직 신청한 작업이 없습니다" body="슬라이싱된 3MF 파일이나 STL 모델로 첫 출력을 신청해 보세요." action={<Link href="/upload" className="button button-primary">출력 신청하기</Link>} />
      ) : (
        <div className="card table-wrap">
          <table className="table jobs-table">
            <thead><tr><th>파일</th><th>상태</th><th>프린터</th><th>신청 시각</th><th>크기</th><th><span className="sr-only">작업</span></th></tr></thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td><div className="job-file"><strong className="truncate">{job.filename}</strong>{job.user_notes ? <span className="truncate">{job.user_notes}</span> : null}{job.status === "rejected" ? <p className="job-rejection-reason">거절 사유: {job.admin_notes || "등록된 사유가 없습니다"}</p> : null}</div></td>
                  <td><StatusBadge status={job.status} suffix={
                    job.status === "printing" ? `${printProgress(job) ?? 0}%`
                    : job.status === "queued" && job.queue_position ? `#${job.queue_position}`
                    : undefined
                  } /></td>
                  <td>{job.printer?.name ?? `#${job.printer_id}`}</td>
                  <td>{formatDate(job.created_at)}</td>
                  <td>{formatBytes(job.file_size)}</td>
                  <td><div className="table-actions"><JobPreview job={job} />{["pending_approval", "queued"].includes(job.status) ? <button className={`button button-danger button-small ${busy === job.id ? "button-loading" : ""}`} disabled={busy === job.id} onClick={() => cancel(job)}>취소</button> : null}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
