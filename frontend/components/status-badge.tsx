import type { JobStatus, PrinterStatus } from "@/lib/types";

const labels: Record<JobStatus | PrinterStatus, string> = {
  processing: "슬라이싱 중",
  pending_approval: "승인 대기",
  queued: "대기열",
  printing: "출력 중",
  awaiting_clear: "베드 정리 대기",
  completed: "완료",
  failed: "실패",
  rejected: "거절",
  canceled: "취소",
  idle: "사용 가능",
  paused: "일시 정지",
  error: "오류",
  offline: "오프라인",
};

export function StatusBadge({ status, suffix }: { status: keyof typeof labels; suffix?: string }) {
  return (
    <span className={`status status-${status}`}>
      {labels[status]}
      {suffix ? ` ${suffix}` : ""}
    </span>
  );
}
