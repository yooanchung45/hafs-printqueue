"""DB 연결 + 세션 관리.

SQLAlchemy 2.0 async + SQLite.
라우트에서 `Depends(get_db)`로 세션 받아 사용.
"""
from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


# 비동기 엔진
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # True로 바꾸면 SQL 쿼리 다 로그에 찍힘 (디버그용)
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _):
    """Enable WAL mode so reads don't block behind status-sync writes."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")   # fsync only at checkpoint
    cur.execute("PRAGMA busy_timeout=5000")    # wait up to 5s instead of failing
    cur.execute("PRAGMA cache_size=10000")     # ~10 MB page cache
    cur.close()

# 세션 팩토리
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# 모든 모델이 상속할 기반 클래스
class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI 의존성: 라우트마다 새 DB 세션 발급, 끝나면 자동 close."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """앱 시작 시 호출. 테이블 없으면 만듦."""
    # 모델을 여기서 import해야 메타데이터에 등록됨 (순환 import 방지)
    import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        job_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("jobs")
            }
        )
        if "failure_acknowledged" not in job_columns:
            await conn.execute(
                text(
                    "ALTER TABLE jobs ADD COLUMN "
                    "failure_acknowledged BOOLEAN NOT NULL DEFAULT 0"
                )
            )

async def seed_printers():
    """프린터가 하나도 없으면 기본 2대 등록 (개발용)."""
    from sqlalchemy import select
    from models import Printer, PrinterStatus

    async with async_session_maker() as session:
        result = await session.execute(select(Printer))
        existing = result.scalars().all()

        if existing:
            return  # 이미 있으면 안 함

        printers = [
            Printer(
                name="A1-1번",
                serial=None,
                ip=None,
                access_code=None,
                status=PrinterStatus.OFFLINE,
            ),
            Printer(
                name="A1-2번",
                serial=None,
                ip=None,
                access_code=None,
                status=PrinterStatus.OFFLINE,
            ),
        ]

        for p in printers:
            session.add(p)
        await session.commit()
async def seed_filament_slots():
    """AMS 슬롯 초기 데이터 (Mock).

    실제 프린터가 연결되면 이 데이터는 MQTT 게이트웨이가 덮어씀.
    지금은 UI 개발용 더미 데이터.
    """
    from sqlalchemy import select
    from models import FilamentSlot, Printer

    async with async_session_maker() as session:
        result = await session.execute(select(FilamentSlot))
        existing = result.scalars().all()
        if existing:
            return

        result = await session.execute(select(Printer).order_by(Printer.id))
        printers = result.scalars().all()

        if not printers:
            return

        mock_data_p1 = [
            ("PLA", "#FFFFFF", "흰색", 80),
            ("PLA", "#000000", "검정", 65),
            ("PLA", "#FF3B30", "빨강", 40),
            (None, None, None, None),
        ]
        mock_data_p2 = [
            ("PLA", "#8E8E93", "회색", 90),
            ("PETG", "#34C759", "초록", 55),
            (None, None, None, None),
            (None, None, None, None),
        ]

        for printer_idx, printer in enumerate(printers[:2]):
            data = mock_data_p1 if printer_idx == 0 else mock_data_p2
            for slot_idx, (mat, color, name, pct) in enumerate(data):
                slot = FilamentSlot(
                    printer_id=printer.id,
                    slot_index=slot_idx,
                    material_type=mat,
                    color_hex=color,
                    color_name=name,
                    remaining_percent=pct,
                    is_empty=1 if mat is None else 0,
                )
                session.add(slot)
        await session.commit()
