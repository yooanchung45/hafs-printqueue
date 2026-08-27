"use client";

import Link from "next/link";
import { ArrowLeft, Download } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { api, ApiError, formatDate } from "@/lib/api";
import type { Job } from "@/lib/types";

type Report = {
  year_options: number[];
  month_options: number[];
  view_year: number;
  view_month: number;
  stats: { total: number; completed: number; failed: number; rejected: number; pending: number; success_rate: number; unique_users: number };
  printer_stats: { name: string; total: number; completed: number; failed: number }[];
  top_users: { name: string; email: string; count: number }[];
  jobs: Job[];
};

export default function ReportsPage() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await api<Report>(`/api/admin/reports?year=${year}&month=${month}`)); setError(""); }
    catch (caught) { if (caught instanceof ApiError && caught.status !== 401) setError(caught.message); }
  }, [year, month]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  return (
    <div className="page">
      <header className="page-header"><div><Link href="/admin" className="back-link"><ArrowLeft size={15} /> 관리자 운영</Link><h1>출력 일지</h1><p>월별 사용량, 성공률과 전체 작업 기록을 확인합니다.</p></div><div className="page-actions"><select className="select report-select" value={year} onChange={(event) => setYear(Number(event.target.value))}>{(data?.year_options ?? [year]).map((item) => <option value={item} key={item}>{item}년</option>)}</select><select className="select report-select" value={month} onChange={(event) => setMonth(Number(event.target.value))}>{Array.from({ length: 12 }, (_, index) => index + 1).map((item) => <option value={item} key={item}>{item}월</option>)}</select><a href={`/api/admin/reports/excel?year=${year}&month=${month}`} className="button button-primary"><Download size={16} /> Excel</a></div></header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      {!data ? <div className="skeleton" /> : <>
        <div className="stats-grid"><div className="stat"><strong>{data.stats.total}</strong><span>전체 신청</span></div><div className="stat"><strong>{data.stats.completed}</strong><span>완료</span></div><div className="stat"><strong>{data.stats.success_rate}%</strong><span>완료율</span></div><div className="stat"><strong>{data.stats.unique_users}</strong><span>이용 학생</span></div></div>
        <div className="grid grid-2 report-summary"><section className="card card-body"><h2>프린터별 작업</h2>{data.printer_stats.length ? data.printer_stats.map((item) => <div className="bar-row" key={item.name}><div><strong>{item.name}</strong><span>{item.completed} 완료 · {item.failed} 실패</span></div><div className="bar"><span style={{ width: `${data.stats.total ? item.total / data.stats.total * 100 : 0}%` }} /></div><strong>{item.total}</strong></div>) : <p className="muted">이번 달 기록이 없습니다.</p>}</section><section className="card card-body"><h2>완료 작업 상위 이용자</h2>{data.top_users.length ? <ol className="top-users">{data.top_users.map((item) => <li key={item.email}><div><strong>{item.name.replace(/^\d+/, "")}</strong><span>{item.email}</span></div><b>{item.count}</b></li>)}</ol> : <p className="muted">완료된 작업이 없습니다.</p>}</section></div>
        <section className="admin-section"><div className="section-heading"><div><h2>{year}년 {month}월 전체 기록</h2><p>신청 시각 기준으로 정렬됩니다.</p></div></div>{data.jobs.length ? <div className="card table-wrap"><table className="table"><thead><tr><th>신청</th><th>학생</th><th>파일</th><th>프린터</th><th>상태</th><th>완료</th></tr></thead><tbody>{data.jobs.map((job) => <tr key={job.id}><td className="muted">{formatDate(job.created_at)}</td><td>{job.owner?.name.replace(/^\d+/, "")}</td><td><strong className="truncate report-filename">{job.filename}</strong></td><td>{job.printer?.name}</td><td><StatusBadge status={job.status} /></td><td className="muted">{formatDate(job.completed_at)}</td></tr>)}</tbody></table></div> : <div className="admin-empty">이 기간의 작업이 없습니다.</div>}</section>
      </>}
    </div>
  );
}
