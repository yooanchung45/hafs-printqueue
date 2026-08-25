"""게시판 라우트."""
import re
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import filters as _filters
from auth import require_user
from db import get_db
from models import Comment, Post, PostAttachment, PostCategory, User, UserRole
from notifications import notify_new_question

router = APIRouter()
templates = Jinja2Templates(directory="templates")
_filters.register(templates)

BOARD_UPLOAD_DIR = Path("static/board")
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}
MAX_ATTACH_BYTES = 10 * 1024 * 1024  # 10 MB

_URL_RE = re.compile(r'(https?://\S+)')


def _autolink(text):
    """Escape HTML, convert newlines to <br>, make URLs clickable."""
    if not text:
        return Markup("")
    safe = str(escape(text)).replace('\n', '<br>\n')
    return Markup(_URL_RE.sub(
        r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
        safe
    ))


templates.env.filters['autolink'] = _autolink


def _build_tree(comments):
    by_id = {c.id: {"comment": c, "children": []} for c in comments}
    roots = []
    for c in comments:
        if c.parent_id is None:
            roots.append(by_id[c.id])
        elif c.parent_id in by_id:
            by_id[c.parent_id]["children"].append(by_id[c.id])
    return roots


def _flatten(nodes, depth=0, out=None):
    if out is None:
        out = []
    for node in nodes:
        out.append({"comment": node["comment"], "depth": depth})
        _flatten(node["children"], depth + 1, out)
    return out


# ── GET /board ────────────────────────────────────────────────────────────────

@router.get("/board", response_class=HTMLResponse)
async def board_list(
    request: Request,
    category: str = None,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Post).order_by(Post.pinned.desc(), Post.created_at.desc())
    if category in ("announcement", "question", "free"):
        query = query.where(Post.category == PostCategory(category))

    result = await db.execute(query)
    posts = result.scalars().all()

    users_map, comment_counts = {}, {}
    if posts:
        author_ids = {p.author_id for p in posts}
        u_res = await db.execute(select(User).where(User.id.in_(author_ids)))
        users_map = {u.id: u for u in u_res.scalars().all()}

        post_ids = [p.id for p in posts]
        c_res = await db.execute(
            select(Comment.post_id, func.count(Comment.id))
            .where(Comment.post_id.in_(post_ids))
            .group_by(Comment.post_id)
        )
        comment_counts = dict(c_res.all())

    return templates.TemplateResponse("board.html", {
        "request": request, "user": user,
        "posts": posts, "users": users_map,
        "comment_counts": comment_counts,
        "active_category": category or "all",
    })


# ── GET /board/new ────────────────────────────────────────────────────────────

@router.get("/board/new", response_class=HTMLResponse)
async def board_new_form(
    request: Request,
    category: str = "free",
    user: User = Depends(require_user),
):
    if category not in ("announcement", "question", "free"):
        category = "free"
    return templates.TemplateResponse("board_new.html", {
        "request": request, "user": user, "default_category": category,
    })


# ── POST /board/new ───────────────────────────────────────────────────────────

@router.post("/board/new")
async def board_create_post(
    title: str = Form(...),
    body: str = Form(...),
    category: str = Form(...),
    pinned: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if category not in ("announcement", "question", "free"):
        category = "free"
    if category == "announcement" and user.role != UserRole.ADMIN:
        raise HTTPException(403, "공지는 관리자만 작성할 수 있습니다")

    BOARD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    attachments = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            continue
        content = await f.read()
        if len(content) > MAX_ATTACH_BYTES:
            continue
        stored_name = uuid.uuid4().hex + ext
        (BOARD_UPLOAD_DIR / stored_name).write_bytes(content)
        attachments.append(PostAttachment(
            original_name=f.filename,
            stored_name=stored_name,
            mime_type=f.content_type,
        ))

    post = Post(
        title=title.strip(),
        body=body.strip(),
        category=PostCategory(category),
        author_id=user.id,
        pinned=(pinned == "on" and user.role == UserRole.ADMIN),
        attachments=attachments,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    if post.category == PostCategory.QUESTION:
        notify_new_question(user.name, post.title, post.id)
    return RedirectResponse(url=f"/board/{post.id}", status_code=303)


# ── GET /board/{post_id} ──────────────────────────────────────────────────────

@router.get("/board/{post_id}", response_class=HTMLResponse)
async def board_post_detail(
    post_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(404, "게시글을 찾을 수 없습니다")

    c_res = await db.execute(
        select(Comment).where(Comment.post_id == post_id).order_by(Comment.created_at)
    )
    comments = c_res.scalars().all()
    flat_comments = _flatten(_build_tree(comments))

    a_res = await db.execute(
        select(PostAttachment).where(PostAttachment.post_id == post_id)
    )
    attachments = a_res.scalars().all()

    author_ids = {post.author_id} | {fc["comment"].author_id for fc in flat_comments}
    u_res = await db.execute(select(User).where(User.id.in_(author_ids)))
    users_map = {u.id: u for u in u_res.scalars().all()}

    return templates.TemplateResponse("board_post.html", {
        "request": request, "user": user,
        "post": post, "flat_comments": flat_comments,
        "attachments": attachments, "users": users_map,
    })


# ── POST /board/{post_id}/comment ─────────────────────────────────────────────

@router.post("/board/{post_id}/comment")
async def board_add_comment(
    post_id: int,
    body: str = Form(...),
    parent_id: Optional[int] = Form(None),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Post).where(Post.id == post_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(404)

    db.add(Comment(
        post_id=post_id,
        parent_id=parent_id,
        author_id=user.id,
        body=body.strip(),
    ))
    await db.commit()
    return RedirectResponse(url=f"/board/{post_id}", status_code=303)


# ── POST /board/{post_id}/delete ──────────────────────────────────────────────

@router.post("/board/{post_id}/delete")
async def board_delete_post(
    post_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(404)
    if post.author_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(403)

    # Delete attachment files from disk
    a_res = await db.execute(select(PostAttachment).where(PostAttachment.post_id == post_id))
    for att in a_res.scalars().all():
        (BOARD_UPLOAD_DIR / att.stored_name).unlink(missing_ok=True)

    await db.execute(delete(Comment).where(Comment.post_id == post_id))
    await db.execute(delete(PostAttachment).where(PostAttachment.post_id == post_id))
    await db.execute(delete(Post).where(Post.id == post_id))
    await db.commit()
    return RedirectResponse(url="/board", status_code=303)


# ── POST /board/comment/{comment_id}/delete ───────────────────────────────────

@router.post("/board/comment/{comment_id}/delete")
async def board_delete_comment(
    comment_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Comment).where(Comment.id == comment_id))
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(404)
    if comment.author_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(403)

    post_id = comment.post_id
    await db.execute(delete(Comment).where(Comment.id == comment_id))
    await db.commit()
    return RedirectResponse(url=f"/board/{post_id}", status_code=303)
