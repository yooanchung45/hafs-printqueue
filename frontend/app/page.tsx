"use client";

import Link from "next/link";
import { ArrowRight, Printer as PrinterIcon, Settings, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { PrinterCard } from "@/components/printer-card";
import { api, ApiError } from "@/lib/api";
import type { Printer, User } from "@/lib/types";

type Dashboard = { user: User; greeting: string; printers: Printer[] };

export default function Home() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await api<Dashboard>("/api/dashboard"));
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
    if (!data) return;
    const timer = window.setInterval(async () => {
      try {
        const states = await api<Printer[]>("/api/printers/status");
        setData((current) => current ? {
          ...current,
          printers: current.printers.map((printer) => ({
            ...printer,
            ...states.find((state) => state.id === printer.id),
            slots: printer.slots,
            jobs: printer.jobs,
          })),
        } : current);
      } catch { /* keep the last known status */ }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [data]);

  if (!data) return <div className="page"><div className="skeleton" />{error ? <div className="notice notice-danger error-banner">{error}</div> : null}</div>;

  return (
    <div className="page">
      <section className="dashboard-lead">
        <h1>{data.greeting}</h1>
        <div className="page-actions">
          <Link href="/upload" className="button button-primary"><Upload size={16} /> 새 출력 신청</Link>
          <Link href="/jobs" className="button button-secondary">내 작업 확인 <ArrowRight size={16} /></Link>
          {data.user.role === "admin" ? (
            <Link href="/admin" className="button button-secondary"><Settings size={16} /> 관리자 페이지</Link>
          ) : null}
        </div>
      </section>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      {data.printers.length ? (
        <div className="printer-grid">
          {data.printers.map((printer) => <PrinterCard key={printer.id} printer={printer} userId={data.user.id} />)}
        </div>
      ) : (
        <EmptyState icon={PrinterIcon} title="등록된 프린터가 없습니다" body="관리자가 프린터를 등록하면 여기에 상태가 표시됩니다." />
      )}
    </div>
  );
}
