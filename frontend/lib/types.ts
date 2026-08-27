export type Role = "student" | "admin";
export type PrinterStatus = "idle" | "printing" | "paused" | "error" | "offline";
export type JobStatus =
  | "processing"
  | "pending_approval"
  | "queued"
  | "printing"
  | "awaiting_clear"
  | "completed"
  | "failed"
  | "rejected"
  | "canceled";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  created_at: string | null;
}

export interface FilamentSlot {
  id: number;
  printer_id: number;
  slot_index: number;
  material_type: string | null;
  color_hex: string | null;
  color_name: string | null;
  remaining_percent: number | null;
  is_empty: boolean;
}

export interface Printer {
  id: number;
  name: string;
  serial: string | null;
  ip: string | null;
  access_code?: string | null;
  has_access_code: boolean;
  status: PrinterStatus;
  current_job_id: number | null;
  progress: number | null;
  nozzle_temp: number | null;
  bed_temp: number | null;
  queue_count?: number;
  slots?: FilamentSlot[];
  jobs?: Job[];
  failed_jobs?: Job[];
}

export interface Job {
  id: number;
  user_id: number;
  printer_id: number;
  filename: string;
  file_size: number | null;
  status: JobStatus;
  queue_position: number | null;
  ams_slot: number | null;
  estimated_minutes: number | null;
  created_at: string | null;
  approved_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  user_notes: string | null;
  admin_notes: string | null;
  failure_acknowledged?: boolean;
  preview_kind: "image" | "stl";
  owner?: User;
  printer?: Pick<Printer, "id" | "name">;
  status_label?: string;
}

export type PostCategory = "announcement" | "question" | "free";

export interface BoardPost {
  id: number;
  title: string;
  body: string;
  category: PostCategory;
  author_id: number;
  author?: User;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  comment_count?: number;
}

export interface Comment {
  id: number;
  post_id: number;
  parent_id: number | null;
  author_id: number;
  author?: User;
  body: string;
  created_at: string;
  depth: number;
}

export interface Attachment {
  id: number;
  post_id: number;
  original_name: string;
  mime_type: string | null;
  url: string;
}
