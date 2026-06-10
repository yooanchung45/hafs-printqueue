"""DB 모델 정의."""
import enum
from datetime import datetime
from sqlalchemy import (
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
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    PRINTING = "printing"
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

    user = relationship("User", back_populates="jobs")
    printer = relationship("Printer", back_populates="jobs", foreign_keys=[printer_id])


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
