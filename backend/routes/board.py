"""Authenticated board API routes."""
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_serializers import attachment_dict, comment_dict, post_dict, user_dict
from auth import require_user
from config import settings
from db import get_db
from models import Comment, Post, PostAttachment, PostCategory, User, UserRole
from notifications import notify_new_question


router = APIRouter(prefix="/api/board", tags=["board"])
BOARD_UPLOAD_DIR = Path(settings.BOARD_UPLOAD_DIR)
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}
MAX_ATTACH_BYTES = 10 * 1024 * 1024


def _build_tree(comments):
    by_id = {comment.id: {"comment": comment, "children": []} for comment in comments}
    roots = []
    for comment in comments:
        if comment.parent_id is None:
            roots.append(by_id[comment.id])
        elif comment.parent_id in by_id:
            by_id[comment.parent_id]["children"].append(by_id[comment.id])
    return roots


def _flatten(nodes, depth=0, output=None):
    output = [] if output is None else output
    for node in nodes:
        output.append({"comment": node["comment"], "depth": depth})
        _flatten(node["children"], depth + 1, output)
    return output


@router.get("")
async def board_list(
    category: str | None = None,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Post).order_by(Post.pinned.desc(), Post.created_at.desc())
    if category in {item.value for item in PostCategory}:
        query = query.where(Post.category == PostCategory(category))
    posts = (await db.execute(query)).scalars().all()
    users_map, comment_counts = {}, {}
    if posts:
        users = (
            await db.execute(select(User).where(User.id.in_({post.author_id for post in posts})))
        ).scalars().all()
        users_map = {row.id: row for row in users}
        counts = await db.execute(
            select(Comment.post_id, func.count(Comment.id))
            .where(Comment.post_id.in_([post.id for post in posts]))
            .group_by(Comment.post_id)
        )
        comment_counts = dict(counts.all())
    return {
        "user": user_dict(user),
        "active_category": category or "all",
        "posts": [
            post_dict(
                post,
                author=users_map.get(post.author_id),
                comment_count=comment_counts.get(post.id, 0),
            )
            for post in posts
        ],
    }


@router.get("/new")
async def board_new_options(
    category: str = "free",
    user: User = Depends(require_user),
):
    if category not in {item.value for item in PostCategory}:
        category = "free"
    categories = ["question", "free"]
    if user.role == UserRole.ADMIN:
        categories.insert(0, "announcement")
    return {"default_category": category, "categories": categories, "user": user_dict(user)}


@router.post("/new")
async def board_create_post(
    title: str = Form(...),
    body: str = Form(...),
    category: str = Form(...),
    pinned: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if not title.strip() or not body.strip():
        raise HTTPException(422, "제목과 내용을 입력해 주세요")
    if category not in {item.value for item in PostCategory}:
        category = "free"
    if category == "announcement" and user.role != UserRole.ADMIN:
        raise HTTPException(403, "공지는 관리자만 작성할 수 있습니다")

    BOARD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    attachments = []
    for file in files:
        if not file.filename:
            continue
        extension = Path(file.filename).suffix.lower()
        if extension not in ALLOWED_EXTS:
            raise HTTPException(422, f"지원하지 않는 첨부파일입니다: {file.filename}")
        content = await file.read()
        if len(content) > MAX_ATTACH_BYTES:
            raise HTTPException(413, f"첨부파일은 10MB를 넘을 수 없습니다: {file.filename}")
        stored_name = uuid.uuid4().hex + extension
        (BOARD_UPLOAD_DIR / stored_name).write_bytes(content)
        attachments.append(
            PostAttachment(
                original_name=file.filename,
                stored_name=stored_name,
                mime_type=file.content_type,
            )
        )
    post = Post(
        title=title.strip(),
        body=body.strip(),
        category=PostCategory(category),
        author_id=user.id,
        pinned=pinned == "on" and user.role == UserRole.ADMIN,
        attachments=attachments,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    if post.category == PostCategory.QUESTION:
        notify_new_question(user.name, post.title, post.id)
    return {"post": post_dict(post, author=user)}


@router.get("/attachments/{attachment_id}")
async def board_attachment(
    attachment_id: int,
    _: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    attachment = (
        await db.execute(
            select(PostAttachment).where(PostAttachment.id == attachment_id)
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise HTTPException(404, "첨부파일을 찾을 수 없습니다")
    path = BOARD_UPLOAD_DIR / attachment.stored_name
    if not path.exists():
        raise HTTPException(404, "첨부파일이 삭제되었습니다")
    disposition = f"inline; filename*=UTF-8''{quote(attachment.original_name)}"
    return FileResponse(
        path,
        media_type=attachment.mime_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.get("/{post_id}")
async def board_post_detail(
    post_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    post = (
        await db.execute(select(Post).where(Post.id == post_id))
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(404, "게시글을 찾을 수 없습니다")
    comments = (
        await db.execute(
            select(Comment).where(Comment.post_id == post_id).order_by(Comment.created_at)
        )
    ).scalars().all()
    flat_comments = _flatten(_build_tree(comments))
    attachments = (
        await db.execute(
            select(PostAttachment).where(PostAttachment.post_id == post_id)
        )
    ).scalars().all()
    author_ids = {post.author_id} | {item["comment"].author_id for item in flat_comments}
    authors = (
        await db.execute(select(User).where(User.id.in_(author_ids)))
    ).scalars().all()
    users_map = {author.id: author for author in authors}
    return {
        "user": user_dict(user),
        "post": post_dict(post, author=users_map.get(post.author_id)),
        "comments": [
            comment_dict(
                item["comment"],
                author=users_map.get(item["comment"].author_id),
                depth=item["depth"],
            )
            for item in flat_comments
        ],
        "attachments": [attachment_dict(attachment) for attachment in attachments],
    }


@router.post("/{post_id}/comment")
async def board_add_comment(
    post_id: int,
    body: str = Form(...),
    parent_id: Optional[int] = Form(None),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.strip():
        raise HTTPException(422, "댓글 내용을 입력해 주세요")
    if (
        await db.execute(select(Post).where(Post.id == post_id))
    ).scalar_one_or_none() is None:
        raise HTTPException(404, "게시글을 찾을 수 없습니다")
    if parent_id is not None:
        parent = (
            await db.execute(
                select(Comment).where(Comment.id == parent_id, Comment.post_id == post_id)
            )
        ).scalar_one_or_none()
        if parent is None:
            raise HTTPException(422, "답글 대상이 올바르지 않습니다")
    comment = Comment(
        post_id=post_id,
        parent_id=parent_id,
        author_id=user.id,
        body=body.strip(),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {"comment": comment_dict(comment, author=user)}


@router.delete("/{post_id}")
@router.post("/{post_id}/delete", include_in_schema=False)
async def board_delete_post(
    post_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    post = (
        await db.execute(select(Post).where(Post.id == post_id))
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(404, "게시글을 찾을 수 없습니다")
    if post.author_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(403, "이 게시글을 삭제할 수 없습니다")
    attachments = (
        await db.execute(
            select(PostAttachment).where(PostAttachment.post_id == post_id)
        )
    ).scalars().all()
    for attachment in attachments:
        (BOARD_UPLOAD_DIR / attachment.stored_name).unlink(missing_ok=True)
    await db.execute(delete(Comment).where(Comment.post_id == post_id))
    await db.execute(delete(PostAttachment).where(PostAttachment.post_id == post_id))
    await db.execute(delete(Post).where(Post.id == post_id))
    await db.commit()
    return {"ok": True}


@router.delete("/comment/{comment_id}")
@router.post("/comment/{comment_id}/delete", include_in_schema=False)
async def board_delete_comment(
    comment_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    comment = (
        await db.execute(select(Comment).where(Comment.id == comment_id))
    ).scalar_one_or_none()
    if comment is None:
        raise HTTPException(404, "댓글을 찾을 수 없습니다")
    if comment.author_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(403, "이 댓글을 삭제할 수 없습니다")
    await db.execute(delete(Comment).where(Comment.id == comment_id))
    await db.commit()
    return {"ok": True, "post_id": comment.post_id}
