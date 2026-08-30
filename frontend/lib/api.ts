export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(payload?.detail ?? "요청을 처리하지 못했습니다.", response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function formatDate(value: string | null, withYear = false) {
  if (!value) return "—";
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  return new Intl.DateTimeFormat("ko-KR", {
    ...(withYear ? { year: "numeric" } : {}),
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  }).format(date);
}

/** Time-based print progress (0–99) for a printing job: elapsed since it
 * started, over the slicer's minute estimate. Recomputes every render so it
 * ticks up between polls. null when there's no estimate (e.g. a pre-sliced
 * Bambu file) — callers can fall back to the printer's reported %. */
export function printProgress(job: {
  status: string;
  started_at: string | null;
  estimated_minutes: number | null;
}): number | null {
  if (job.status !== "printing" || !job.started_at || !job.estimated_minutes) return null;
  const started = new Date(job.started_at.endsWith("Z") ? job.started_at : `${job.started_at}Z`).getTime();
  const elapsedMin = (Date.now() - started) / 60000;
  return Math.max(0, Math.min(99, Math.round((elapsedMin / job.estimated_minutes) * 100)));
}

export function formatBytes(value: number | null) {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
