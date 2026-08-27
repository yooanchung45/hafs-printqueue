"use client";

import Link from "next/link";
import { MessageSquare, PenLine, Pin } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { api, ApiError, formatDate } from "@/lib/api";
import type { BoardPost } from "@/lib/types";

const categories = [{ value: "all", label: "전체" }, { value: "announcement", label: "공지" }, { value: "question", label: "질문" }, { value: "free", label: "자유" }];
const categoryLabels = { announcement: "공지", question: "질문", free: "자유" };

export default function BoardPage() {
  const params = useSearchParams();
  const category = params.get("category") ?? "all";
  const [posts, setPosts] = useState<BoardPost[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => api<{ posts: BoardPost[] }>(`/api/board${category === "all" ? "" : `?category=${category}`}`)
      .then((data) => { setPosts(data.posts); setError(""); })
      .catch((caught) => caught instanceof ApiError && caught.status !== 401 && setError(caught.message)), 0);
    return () => window.clearTimeout(timer);
  }, [category]);
  return (
    <div className="page">
      <header className="page-header"><div><h1>게시판</h1></div><Link href={`/board/new${category !== "all" ? `?category=${category}` : ""}`} className="button button-primary"><PenLine size={16} /> 새 글</Link></header>
      <nav className="tabs" aria-label="게시판 카테고리">{categories.map((item) => <Link key={item.value} href={item.value === "all" ? "/board" : `/board?category=${item.value}`} className={category === item.value ? "tab tab-active" : "tab"}>{item.label}</Link>)}</nav>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      {posts === null ? <div className="skeleton" /> : posts.length === 0 ? <EmptyState icon={MessageSquare} title="게시글이 없습니다" body="궁금한 점이나 공유하고 싶은 내용을 첫 글로 남겨보세요." action={<Link href="/board/new" className="button button-primary">첫 글 작성</Link>} /> : (
        <div className="board-list">
          {posts.map((post) => (
            <Link href={`/board/${post.id}`} className={post.pinned ? "board-row board-row-pinned" : "board-row"} key={post.id}>
              <div className="board-row-main">{post.pinned ? <Pin size={14} aria-label="고정" /> : null}<span className={`category category-${post.category}`}>{categoryLabels[post.category]}</span><strong className="truncate">{post.title}</strong></div>
              <div className="board-row-meta"><span>{post.author?.name ?? "—"}</span><time>{formatDate(post.created_at)}</time><span><MessageSquare size={12} /> {post.comment_count ?? 0}</span></div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
