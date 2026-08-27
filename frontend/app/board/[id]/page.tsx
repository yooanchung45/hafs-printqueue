"use client";
/* eslint-disable @next/next/no-img-element */

import Link from "next/link";
import { ArrowLeft, FileText, MessageSquare, Pin, Reply, Trash2 } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useState } from "react";

import { api, ApiError, formatDate } from "@/lib/api";
import type { Attachment, BoardPost, Comment, User } from "@/lib/types";

type Detail = { user: User; post: BoardPost; comments: Comment[]; attachments: Attachment[] };
const categoryLabels = { announcement: "공지", question: "질문", free: "자유" };
const urlPattern = /(https?:\/\/[^\s]+)/g;

function LinkedText({ children }: { children: string }) {
  return <>{children.split(urlPattern).map((part, index) => part.match(/^https?:\/\//) ? <a href={part} target="_blank" rel="noopener noreferrer" key={index}>{part}</a> : <Fragment key={index}>{part}</Fragment>)}</>;
}

export default function PostPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [data, setData] = useState<Detail | null>(null);
  const [body, setBody] = useState("");
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const [replyBody, setReplyBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await api<Detail>(`/api/board/${id}`)); setError(""); }
    catch (caught) { if (caught instanceof ApiError && caught.status !== 401) setError(caught.message); }
  }, [id]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  const comment = async (text: string, parentId?: number) => {
    if (!text.trim()) return;
    setBusy(true); const form = new FormData(); form.append("body", text); if (parentId) form.append("parent_id", String(parentId));
    try { await api(`/api/board/${id}/comment`, { method: "POST", body: form }); setBody(""); setReplyBody(""); setReplyTo(null); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "댓글을 등록하지 못했습니다."); } finally { setBusy(false); }
  };
  const deletePost = async () => {
    if (!data || !window.confirm("이 게시글을 삭제할까요?")) return;
    try { await api(`/api/board/${id}`, { method: "DELETE" }); router.push("/board"); } catch (caught) { setError(caught instanceof Error ? caught.message : "삭제하지 못했습니다."); }
  };
  const deleteComment = async (commentId: number) => {
    if (!window.confirm("이 댓글을 삭제할까요?")) return;
    try { await api(`/api/board/comment/${commentId}`, { method: "DELETE" }); await load(); } catch (caught) { setError(caught instanceof Error ? caught.message : "삭제하지 못했습니다."); }
  };
  if (!data) return <div className="page page-narrow">{error ? <div className="notice notice-danger">{error}</div> : <div className="skeleton" />}</div>;
  const canDeletePost = data.user.id === data.post.author_id || data.user.role === "admin";
  return (
    <div className="page page-narrow">
      <div className="post-nav"><Link href="/board" className="button button-ghost"><ArrowLeft size={16} /> 게시판</Link><span className={`category category-${data.post.category}`}>{categoryLabels[data.post.category]}</span>{data.post.pinned ? <span className="muted"><Pin size={13} /> 고정됨</span> : null}</div>
      {error ? <div className="notice notice-danger error-banner">{error}</div> : null}
      <article className="card post-card">
        <header className="post-header"><h1>{data.post.title}</h1><div className="post-meta"><strong>{data.post.author?.name}</strong><time>{formatDate(data.post.created_at, true)}</time><span>댓글 {data.comments.length}개</span>{canDeletePost ? <button className="button button-danger button-small" onClick={deletePost}><Trash2 size={13} /> 삭제</button> : null}</div></header>
        <div className="post-body"><LinkedText>{data.post.body}</LinkedText></div>
        {data.attachments.length ? <div className="attachments"><h2>첨부파일</h2><div className="attachment-grid">{data.attachments.map((attachment) => attachment.mime_type?.startsWith("image/") ? <a href={attachment.url} target="_blank" rel="noopener noreferrer" className="attachment-image" key={attachment.id}><img src={attachment.url} alt={attachment.original_name} /><span>{attachment.original_name}</span></a> : <a href={attachment.url} className="attachment-file" key={attachment.id}><FileText size={17} /><span className="truncate">{attachment.original_name}</span></a>)}</div></div> : null}
      </article>
      <section className="comments" aria-labelledby="comments-title">
        <h2 id="comments-title"><MessageSquare size={17} /> 댓글 {data.comments.length}</h2>
        <div className="comment-list">{data.comments.map((item) => {
          const canDelete = data.user.id === item.author_id || data.user.role === "admin";
          return <article className="comment" style={{ "--depth": Math.min(item.depth, 4) } as React.CSSProperties} key={item.id}><header><strong>{item.author?.name}</strong><time>{formatDate(item.created_at)}</time><button className="button button-ghost button-small" onClick={() => { setReplyTo(replyTo === item.id ? null : item.id); setReplyBody(""); }}><Reply size={13} /> 답글</button>{canDelete ? <button className="button button-ghost button-small danger-text" onClick={() => deleteComment(item.id)}>삭제</button> : null}</header><p><LinkedText>{item.body}</LinkedText></p>{replyTo === item.id ? <div className="reply-form"><textarea className="textarea" value={replyBody} onChange={(event) => setReplyBody(event.target.value)} placeholder={`${item.author?.name}님에게 답글`} autoFocus /><div className="form-actions"><button className="button button-primary button-small" disabled={busy} onClick={() => comment(replyBody, item.id)}>답글 등록</button><button className="button button-secondary button-small" onClick={() => setReplyTo(null)}>취소</button></div></div> : null}</article>;
        })}</div>
        <div className="card card-body new-comment"><h3>댓글 작성</h3><textarea className="textarea" value={body} onChange={(event) => setBody(event.target.value)} placeholder="댓글을 입력하세요" /><button className={`button button-primary ${busy ? "button-loading" : ""}`} disabled={busy || !body.trim()} onClick={() => comment(body)}>댓글 등록</button></div>
      </section>
    </div>
  );
}
