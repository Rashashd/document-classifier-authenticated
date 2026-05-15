export type UserRole = "admin" | "reviewer" | "auditor";

export type BatchStatus = "pending" | "processing" | "done" | "failed";

export type DocumentLabel =
  | "letter"
  | "memo"
  | "email"
  | "file_folder"
  | "form"
  | "handwritten"
  | "invoice"
  | "advertisement"
  | "budget"
  | "news_article"
  | "presentation"
  | "scientific_publication"
  | "scientific_report"
  | "specification"
  | "resume"
  | "questionnaire";

export const DOCUMENT_LABELS: DocumentLabel[] = [
  "letter",
  "memo",
  "email",
  "file_folder",
  "form",
  "handwritten",
  "invoice",
  "advertisement",
  "budget",
  "news_article",
  "presentation",
  "scientific_publication",
  "scientific_report",
  "specification",
  "resume",
  "questionnaire",
];

export interface UserRead {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  role: UserRole;
  created_at: string;
}

export interface BatchRead {
  id: string;
  sftp_path: string;
  status: BatchStatus;
  owner_id: string | null;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface BatchListResponse {
  items: BatchRead[];
  total: number;
  skip: number;
  limit: number;
}

export interface BatchUpdate {
  status?: BatchStatus | null;
}

export interface PredictionRead {
  id: string;
  batch_id: string;
  filename: string;
  label: DocumentLabel;
  confidence: number;
  overlay_path: string | null;
  created_at: string;
}

export interface PredictionListResponse {
  items: PredictionRead[];
  total: number;
  skip: number;
  limit: number;
}

export interface PredictionUpdate {
  label?: DocumentLabel | null;
  overlay_path?: string | null;
}

export interface AuditEntryRead {
  id: string;
  actor_id: string;
  action: string;
  target: string;
  request_id: string | null;
  timestamp: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
}

export interface ApiError {
  detail: string | { msg: string; type: string }[];
}
