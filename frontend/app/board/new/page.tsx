"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { PostCategory, User } from "@/lib/types";

const labels = { announcement: "공지", question: "질문", free: "자유" };

export default function NewPostPage() {
  const router = useRouter();
  const search = useSearchParams();
  const [options, setOptions] = useState<{ categories: PostCategory[]; user: User } | null>(null);
  const [category, setCategory] = useState<PostCategory>((search.get("category") as PostCategory) ?? "free");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { api<{ categories: PostCategory[]; user: User }>(`/api/board/new?category=${category}`).then((data) => { setOptions(data); if (!data.categories.includes(category)) setCategory("free"); }).catch((caught) => caught instanceof ApiError && caught.status !== 401 && setError(caught.message)); }, [category]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(); form.append("category", category); form.append("title", title); form.append("body", body); if (pinned) form.append("pinned", "on"); files.forEach((file) => form.append("files", file));
    try { const result = await api<{ post: { id: number } }>("/api/board/new", { method: "POST", body: form }); router.push(`/board/${result.post.id}`); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "게시하지 못했습니다."); setBusy(false); }
  };
  return (
    <div className="page page-narrow">
      <header className="page-header"><div><h1>새 글 작성</h1><p>질문에는 사용한 파일 형식과 발생한 상황을 함께 적어주세요.</p></div></header>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <form className="card card-body post-form" onSubmit={submit}>
        <div className="field"><label htmlFor="category">카테고리</label><select id="category" className="select" value={category} onChange={(event) => setCategory(event.target.value as PostCategory)}>{options?.categories.map((item) => <option value={item} key={item}>{labels[item]}</option>)}</select></div>
        <div className="field"><label htmlFor="title">제목</label><input id="title" className="input" maxLength={300} required value={title} onChange={(event) => setTitle(event.target.value)} placeholder="제목을 입력하세요" /></div>
        <div className="field"><label htmlFor="body">내용</label><textarea id="body" className="textarea post-editor" required value={body} onChange={(event) => setBody(event.target.value)} placeholder="내용을 입력하세요" /></div>
        {options?.user.role === "admin" && category === "announcement" ? <label className="checkbox"><input type="checkbox" checked={pinned} onChange={(event) => setPinned(event.target.checked)} /> 상단에 고정</label> : null}
        <div className="field"><label htmlFor="attachments">첨부파일 <span className="muted">· 이미지 또는 PDF, 파일당 10MB</span></label><input id="attachments" className="input file-input" type="file" accept=".jpg,.jpeg,.png,.gif,.webp,.pdf" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} /></div>
        <div className="form-actions"><button className={`button button-primary ${busy ? "button-loading" : ""}`} disabled={busy}>게시하기</button><Link href="/board" className="button button-secondary">취소</Link></div>
      </form>
    </div>
  );
}
