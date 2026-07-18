"""환경변수 기반 설정.

.env 파일의 값을 읽어서 settings 객체로 노출.
"""
import os
from dotenv import load_dotenv

# /app/data가 아니라 /app에서 한 단계 위 (호스트 /srv/printqueue/.env)를 찾도록
# docker-compose에서 env_file로 자동 로드되지만 안전망으로 load_dotenv도 호출
load_dotenv()


class Settings:
    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # 세션 쿠키 서명용
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "")

    # 허용 이메일 도메인 (학교 이메일만)
    ALLOWED_EMAIL_DOMAIN: str = os.getenv("ALLOWED_EMAIL_DOMAIN", "hafs.hs.kr")

    # 관리자 이메일 (쉼표로 여러 개 가능) — env var: ADMIN_EMAILS
    ADMIN_EMAILS_RAW: str = os.getenv("ADMIN_EMAILS", "")

    # 콜백 URL (Google Cloud Console에 등록된 거랑 일치해야 함)
    OAUTH_REDIRECT_URI: str = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")

    # DB 파일 경로 (컨테이너 내부 기준)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////app/data/db.sqlite")

    # 업로드 폴더
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "/app/data/uploads")

    @property
    def admin_emails(self) -> list[str]:
        """쉼표 구분된 관리자 이메일을 리스트로."""
        if not self.ADMIN_EMAILS_RAW:
            return []
        return [e.strip().lower() for e in self.ADMIN_EMAILS_RAW.split(",") if e.strip()]

    def is_admin(self, email: str) -> bool:
        """이메일이 관리자인지."""
        return email.lower() in self.admin_emails


settings = Settings()


# 시작 시 필수 환경변수 확인
def validate():
    required = {
        "GOOGLE_CLIENT_ID": settings.GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": settings.GOOGLE_CLIENT_SECRET,
        "SESSION_SECRET": settings.SESSION_SECRET,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
