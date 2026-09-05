"""DB 모델 정의."""
import enum
from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func, Float)
from sqlalchemy.orm import relationship

from db import Base


class UserRole(str, enum.Enum):
    STUDENT = "student"
    ADMIN = "admin"


class PrinterStatus(str, enum.Enum):
    IDLE = "idle"
    PRINTING = "printing"
    ERROR = "error"
    OFFLINE = "offline"
    PAUSED = "paused"


class JobStatus(str, enum.Enum):
    PROCESSING = "processing"       # STL being sliced in background
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    PRINTING = "printing"
    AWAITING_CLEAR = "awaiting_clear"  # print done; admin must clear the bed
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELED = "canceled"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.STUDENT)
    created_at = Column(DateTime, server_default=func.now())

    jobs = relationship("Job", back_populates="user")


class Printer(Base):
    __tablename__ = "printers"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    serial = Column(String(100), nullable=True)
    ip = Column(String(45), nullable=True)
    access_code = Column(String(20), nullable=True)
    status = Column(SQLEnum(PrinterStatus), nullable=False, default=PrinterStatus.OFFLINE)
    current_job_id = Column(Integer, ForeignKey("jobs.id", use_alter=True), nullable=True)
    progress = Column(Integer, nullable=True)
    nozzle_temp = Column(Float, nullable=True)
    bed_temp = Column(Float, nullable=True)
    # Some units have a physical obstruction (wall, cable, ...) behind them
    # blocking the bed's full rear travel, so the eject sweep needs to push
    # the opposite way on those -- see printer_client.eject_bed.
    eject_reversed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())

    jobs = relationship("Job", back_populates="printer", foreign_keys="Job.printer_id")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    printer_id = Column(Integer, ForeignKey("printers.id"), nullable=False)

    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    file_size = Column(Integer, nullable=True)

    status = Column(SQLEnum(JobStatus), nullable=False, default=JobStatus.PENDING_APPROVAL)
    queue_position = Column(Integer, nullable=True)
    ams_slot = Column(Integer, nullable=True)
    estimated_minutes = Column(Integer, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    approved_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    user_notes = Column(Text, nullable=True)
    admin_notes = Column(Text, nullable=True)
    failure_acknowledged = Column(Boolean, nullable=False, default=False, server_default="0")

    user = relationship("User", back_populates="jobs")
    printer = relationship("Printer", back_populates="jobs", foreign_keys=[printer_id])


class PostCategory(str, enum.Enum):
    ANNOUNCEMENT = "announcement"
    QUESTION = "question"
    FREE = "free"


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    title = Column(String(300), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(SQLEnum(PostCategory), nullable=False, default=PostCategory.FREE)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    pinned = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    author = relationship("User")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    attachments = relationship("PostAttachment", back_populates="post", cascade="all, delete-orphan")


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("comments.id"), nullable=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    post = relationship("Post", back_populates="comments")
    author = relationship("User")


class PostAttachment(Base):
    __tablename__ = "post_attachments"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    original_name = Column(String(500), nullable=False)
    stored_name = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    post = relationship("Post", back_populates="attachments")


class FilamentSlot(Base):
    """AMS 슬롯의 현재 필라멘트 정보."""
    __tablename__ = "filament_slots"

    id = Column(Integer, primary_key=True)
    printer_id = Column(Integer, ForeignKey("printers.id"), nullable=False)
    slot_index = Column(Integer, nullable=False)

    material_type = Column(String(50), nullable=True)
    color_hex = Column(String(7), nullable=True)
    color_name = Column(String(50), nullable=True)
    remaining_percent = Column(Integer, nullable=True)
    is_empty = Column(Integer, default=0)

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    printer = relationship("Printer")
