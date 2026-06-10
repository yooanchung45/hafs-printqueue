"""Google OAuth 인증 + 세션 관리 + 권한 의존성.

흐름:
    /auth/login → Google 로그인 페이지로 리다이렉트
    /auth/callback → Google이 콜백. 이메일 도메인 검증 → User upsert → 세션 쿠키 발급
    /auth/logout → 세션 제거

세션 쿠키: itsdangerous로 서명. 변조 불가.
사용자 식별: 세션 쿠키에 user_id만 저장. 매 요청마다 DB에서 User 조회.
"""
from datetime import datetime
from typing import Optional

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import get_db
from models import User, UserRole


# Authlib OAuth 클라이언트 (Google 자동 검색용 메타데이터 URL)
oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ============================================================
# 라우트 핸들러 (main.py에서 라우트로 등록)
# ============================================================

async def login(request: Request):
    """Google 로그인 페이지로 리다이렉트."""
    redirect_uri = settings.OAUTH_REDIRECT_URI
    return await oauth.google.authorize_redirect(request, redirect_uri)


async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Google에서 인증 후 콜백."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return RedirectResponse(url=f"/?error={e.error}", status_code=302)

    # Google이 반환한 사용자 정보
    userinfo = token.get("userinfo")
    if not userinfo:
        return RedirectResponse(url="/?error=no_userinfo", status_code=302)

    email = userinfo.get("email", "").lower()
    name = userinfo.get("name", "")
    email_verified = userinfo.get("email_verified", False)

    # 이메일 검증
    if not email_verified:
        return RedirectResponse(url="/?error=email_not_verified", status_code=302)

    # 도메인 검증 (학교 이메일만)
    if not email.endswith(f"@{settings.ALLOWED_EMAIL_DOMAIN}") and not settings.is_admin(email):
        return RedirectResponse(url="/?error=domain_not_allowed", status_code=302)

    # 사용자 upsert (있으면 가져오고, 없으면 새로 만듦)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    role = UserRole.ADMIN if settings.is_admin(email) else UserRole.STUDENT

    if user is None:
        user = User(email=email, name=name, role=role)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        # 이름이나 권한이 바뀌었으면 갱신
        changed = False
        if user.name != name:
            user.name = name
            changed = True
        if user.role != role:
            user.role = role
            changed = True
        if changed:
            await db.commit()

    # 세션에 user_id 저장 (SessionMiddleware가 알아서 쿠키로 직렬화)
    request.session["user_id"] = user.id

    return RedirectResponse(url="/", status_code=302)


async def logout(request: Request):
    """세션 제거."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


# ============================================================
# 권한 의존성 (라우트에서 Depends로 사용)
# ============================================================

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """현재 로그인한 사용자. 미인증이면 None.

    사용법:
        async def route(user: User | None = Depends(get_current_user)):
            if user is None:
                return "anonymous"
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        # DB에서 사용자가 사라졌으면 세션도 정리
        request.session.clear()

    return user


async def require_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """로그인 필수. 미인증이면 401.

    사용법:
        async def route(user: User = Depends(require_user)):
            # user는 항상 인증된 상태
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login required",
        )
    return user


async def require_admin(
    user: User = Depends(require_user),
) -> User:
    """관리자 필수. 학생이면 403."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return user
