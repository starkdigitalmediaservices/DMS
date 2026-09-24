export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Folder {
  id: string;
  name: string;
  parent_id?: string | null;
  tenant_id: string;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  is_starred: boolean;
  is_trashed: boolean;
  trashed_at?: string | null;
  color?: string;
}

export interface FolderTreeNode {
  id: string;
  name: string;
  parent_id?: string | null;
  color?: string;
  subfolders?: FolderTreeNode[];
  children?: FolderTreeNode[];
}

export interface DocumentListItem {
  id: string;
  title: string;
  doc_type?: string | null;
  status: string;
  created_at: string;
  folder_id?: string | null;
  is_starred: boolean;
  is_trashed: boolean;
  trashed_at?: string | null;
  file_size_bytes: number;
  current_version_id?: string | null;
  s3_path?: string | null;
  download_url?: string | null;
  quality_flag?: string | null;
  quality_warnings?: string[];
}

export interface DocumentDetailResponse {
  document_id: string;
  title: string;
  doc_type?: string | null;
  status: string;
  created_at: string;
  folder_id?: string | null;
  is_starred: boolean;
  is_trashed: boolean;
  trashed_at?: string | null;
  current_version?: {
    id: string;
    version: number;
    s3_path: string;
    file_size_bytes: number;
    download_url: string;
    created_at: string;
  } | null;
  metadata: Array<{
    key: string;
    value: string;
    source: string;
    confidence_score: number;
    // T05 — where this value came from on the page, when it could be
    // verbatim-located. Empty for metadata written before this shipped,
    // or for a value the extracting LLM paraphrased away from the page's
    // actual printed text (no fabricated location in that case).
    regions?: Array<{ page_number: number; x0: number; y0: number; x1: number; y1: number }>;
  }>;
  versions: Array<{
    id: string;
    version: number;
    s3_path: string;
    file_size_bytes: number;
    download_url: string;
    created_at: string;
  }>;
  // T79 — fuzzy-duplicate candidates found at ingest (embedding similarity
  // on chunk 0). Informational only; null/absent when nothing above threshold.
  possible_duplicate_candidates?: Array<{
    document_id: string;
    title: string;
    similarity: number;
  }> | null;
}

export interface DriveStats {
  total_files: number;
  total_folders: number;
  total_bytes?: number;
  total_size_bytes?: number;
  total_starred: number;
  total_trashed: number;
}

export interface DocumentFact {
  fact_id: string;
  field_name: string;
  value: unknown;
  confidence: number | null;
  status: "machine" | "in_review" | "verified";
  is_handwritten: boolean;
  // Bumped on every value/status change; sent back on edit/confirm so a
  // stale write is a 409 instead of a silent overwrite.
  edit_version?: number;
  page_numbers: number[];
  // True when this field's regions land on more than one physical page —
  // the only reliable, verifiable signal that TS1 (vertical stitching)
  // actually merged a continuation row from a later page into this entry,
  // rather than a heuristic guess re-derived in the UI.
  stitched: boolean;
}

export interface DocumentTableRow {
  page_number: number;
  stitched: boolean;
  needs_review: boolean;
  values: Record<string, unknown>;
  /** column -> id of the fact that value came from */
  fact_ids?: Record<string, string>;
}

export interface DocumentTableViewResponse {
  document_id: string;
  classification_status: string;
  page_header: Record<string, unknown>;
  columns: string[];
  rows: DocumentTableRow[];
  row_count: number;
  page_count?: number;
}

export interface DocumentFactsResponse {
  document_id: string;
  classification_status: string;
  matched_template_id?: string | null;
  facts: DocumentFact[];
  page_count?: number;
  stitched_field_count: number;
  in_review_count: number;
}

export interface SearchResult {
  document_id: string;
  document_name: string;
  download_url: string;
  page_number: number | null;
  snippet: string;
  score: number;
  tags?: string[];
  metadata: Record<string, unknown>;
}

export interface Citation {
  number: number;
  claim: string;
  document_id: string;
  document_name: string;
  page_number: number | null;
  chunk_id?: string | null;
  fact_id?: string | null;
  download_url?: string | null;
}

export interface SearchResponse {
  query: string;
  ai_summary: string;
  results: SearchResult[];
  citations?: Citation[];
  refused?: boolean;
  cached: boolean;
  took_ms: number;
  search_mode?: string;
  hyde_triggered?: boolean;
  reranked?: boolean;
  grounded?: boolean;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  results?: SearchResult[] | null;
  filters?: Record<string, any> | null;
  search_mode?: string;
  created_at: string;
}

export interface ChatSessionListItem {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatSession {
  id: string;
  tenant_id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

export interface TemplateFieldDef {
  name: string;
  type: string;
  required?: boolean;
  role?: string | null;
}

export interface TemplateResponse {
  id: string;
  form_type: string;
  era_label: string;
  field_schema: TemplateFieldDef[];
  layout: string;
  created_at: string;
  updated_at: string;
}

export interface TemplateCreatePayload {
  form_type: string;
  era_label: string;
  field_schema: TemplateFieldDef[];
  layout: string;
}

// T03 — one row of sys_dg_config, the global engineering-threshold table.
export interface SysConfigItem {
  key: string;
  value: number;
  description: string;
  updated_at: string;
}


// IT-admin user & role management (GET/POST/PATCH /api/v1/users).
export interface AdminUserDepartment {
  id: string;
  name: string;
}

export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  created_at: string;
  departments: AdminUserDepartment[];
}

// POST /users only — temp_password is shown to the admin once and never stored.
export interface CreatedAdminUser extends AdminUser {
  temp_password: string;
}

export interface DepartmentMember {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
}

export interface DepartmentFolderGrant {
  folder_id: string;
  name: string;
}

export interface Department {
  id: string;
  name: string;
  created_at: string;
  members: DepartmentMember[];
  folders: DepartmentFolderGrant[];
}

// ---- Review screen (GET/PATCH /documents/{id}/review) ----

export type ReviewStatus = "MACHINE_EXTRACTED" | "EDITED" | "VERIFIED";
export type ReviewBlockType = "heading" | "paragraph" | "table" | "image";

/** Normalised 0-1 box, top-left origin. `page` is 1-indexed. */
export interface ReviewBox {
  page: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ReviewCell {
  fact_id: string | null;
  field_name?: string;
  text: string;
  original: string;
  edited: boolean;
  status: ReviewStatus;
  fact_status?: "machine" | "in_review" | "verified";
  fact_version?: number;
  revertable: boolean;
  changed_elsewhere?: boolean;
  confidence: number | null;
  low_confidence: boolean;
  bbox: ReviewBox | null;
  regions: ReviewBox[];
  history_count: number;
  missing?: boolean;
}

export interface ReviewRow {
  id: string;
  index: number;
  page: number | null;
  added: boolean;
  deleted: boolean;
  flags: string[];
  status: ReviewStatus;
  verified_by: string | null;
  verified_at: string | null;
  cells: ReviewCell[];
  history_count: number;
}

export interface ReviewBlock {
  id: string;
  type: ReviewBlockType;
  order: number;
  title: string | null;
  added: boolean;
  deleted: boolean;
  source_pages: number[];
  /** keyed by page number as a string; only pages the block covers */
  bbox: Record<string, Omit<ReviewBox, "page"> | null>;
  confidence: number | null;
  flags: string[];
  status: ReviewStatus;
  history_count: number;
  headers?: string[];
  rows?: ReviewRow[];
  text?: string;
  original?: string;
  edited?: boolean;
  revertable?: boolean;
  verified_by?: string | null;
  verified_at?: string | null;
}

export interface ReviewDocument {
  document_id: string;
  title: string;
  mime_type: string | null;
  page_count: number;
  builder: string;
  version: number;
  etag: string;
  edit_count: number;
  is_clean: boolean;
  low_confidence_threshold: number;
  blocks: ReviewBlock[];
  permissions: { can_edit: boolean; can_verify: boolean; can_revert_all: boolean };
  revert_all?: { reverted: number; skipped: { fact_id: string; reason: string }[] };
}

export interface ReviewHistoryEntry {
  id: string;
  action: string;
  block_id: string | null;
  row_id: string | null;
  row: number | null;
  col: number | null;
  fact_id: string | null;
  old_value: unknown;
  new_value: unknown;
  user_id: string;
  user_name: string | null;
  created_at: string;
  entry_hash: string;
}

export interface ReviewDocumentSummary {
  document_id: string;
  title: string;
  page_count: number;
  fact_count: number;
  in_review_count: number;
  verified_count: number;
  verified_pct: number;
  review_started: boolean;
  last_reviewed_at: string | null;
  last_reviewed_by: string | null;
}
