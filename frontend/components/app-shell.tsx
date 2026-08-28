"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  BookOpen,
  ClipboardList,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Settings,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { ThemeToggle } from "@/components/theme-toggle";
import type { User } from "@/lib/types";

type Session = { authenticated: boolean; user: User | null };

const navigation = [
  { href: "/", label: "프린터 현황", icon: LayoutDashboard },
  { href: "/upload", label: "출력 신청", icon: Upload },
  { href: "/jobs", label: "내 작업", icon: ClipboardList },
  { href: "/board", label: "게시판", icon: MessageSquare },
  { href: "/guide", label: "슬라이싱 가이드", icon: BookOpen },
];

const loginErrors: Record<string, string> = {
  email_not_verified: "Google 계정의 이메일 인증이 필요합니다.",
  domain_not_allowed: "hafs.hs.kr 학교 계정으로만 로그인할 수 있습니다.",
  no_userinfo: "Google 계정 정보를 불러오지 못했습니다.",
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [session, setSession] = useState<Session | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    api<Session>("/api/session").then(setSession).catch(() => setSession({ authenticated: false, user: null }));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setMenuOpen(false), 0);
    return () => window.clearTimeout(timer);
  }, [pathname]);

  if (!session) {
    return (
      <div className="boot-screen" aria-live="polite">
        <Image className="brand-mark" src="/brand-mark.svg" alt="" width={32} height={32} priority />
        <span className="spinner" />
        <span>PrintQueue를 불러오는 중</span>
      </div>
    );
  }

  if (!session.authenticated || !session.user) {
    const error = searchParams.get("error");
    return (
      <main className="login-page">
        <section className="login-panel" aria-labelledby="login-title">
          <div className="login-brand">
            <Image className="brand-mark" src="/brand-mark.svg" alt="" width={52} height={52} priority />
            <div className="login-brand-copy">
              <h1 id="login-title" data-brand-heading="true">HAFS PrintQueue</h1>
              <span>외대부고 3D 프린터 출력 시스템</span>
            </div>
          </div>
          {error ? <div className="notice notice-danger">{loginErrors[error] ?? `로그인 오류: ${error}`}</div> : null}
          <a className="button button-primary button-large" href="/api/auth/login">
            Google 학교 계정으로 계속
          </a>
        </section>
        <aside className="login-aside" aria-hidden="true">
          <div className="queue-illustration">
            <span className="queue-line queue-line-active" />
            <span className="queue-line" />
            <span className="queue-line" />
            <span className="queue-line queue-line-short" />
          </div>
        </aside>
      </main>
    );
  }

  const navItems = session.user.role === "admin"
    ? [...navigation, { href: "/admin", label: "관리자", icon: Settings }]
    : navigation;

  return (
    <div className="app-frame">
      <header className="mobile-header">
        <Link href="/" className="brand-lockup">
          <Image className="brand-mark" src="/brand-mark.svg" alt="" width={27} height={27} />
          <span>PrintQueue</span>
        </Link>
        <div className="mobile-header-actions">
          <ThemeToggle />
          <button className="icon-button" onClick={() => setMenuOpen((value) => !value)} aria-label="메뉴" aria-expanded={menuOpen}>
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </header>

      <aside className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}>
        <Link href="/" className="brand-lockup sidebar-brand">
          <Image className="brand-wordmark" src="/brand-logo.svg" alt="PrintQueue" width={192} height={46} priority />
        </Link>
        <nav className="sidebar-nav" aria-label="주 메뉴">
          {navItems.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link key={href} href={href} className={`nav-link ${active ? "nav-link-active" : ""}`} aria-current={active ? "page" : undefined}>
                <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
                <span>{label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-account">
          <div className="avatar" aria-hidden="true">{session.user.name.slice(0, 1)}</div>
          <div className="account-copy">
            <strong>{session.user.name}</strong>
            <span>{session.user.role === "admin" ? "관리자" : "학생"}</span>
          </div>
          <ThemeToggle />
          <a href="/api/auth/logout" className="icon-button" aria-label="로그아웃" title="로그아웃">
            <LogOut size={17} />
          </a>
        </div>
      </aside>
      {menuOpen ? <button className="sidebar-backdrop" onClick={() => setMenuOpen(false)} aria-label="메뉴 닫기" /> : null}
      <main className="app-main">{children}</main>
    </div>
  );
}
