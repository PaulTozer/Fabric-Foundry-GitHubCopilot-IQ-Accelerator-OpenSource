export type Role = 'user' | 'assistant';

/** Agent methodology on the wire, including the retained legacy Work IQ path. */
export type MethodId = 'M4' | 'M6' | 'M7';
export type SelectableMethodId = Exclude<MethodId, 'M7'>;

/** Display metadata for the method picker dropdown. */
export const METHOD_OPTIONS: Array<{ id: SelectableMethodId; label: string; hint: string }> = [
  { id: 'M4', label: 'Copilot SDK · BYOK', hint: 'GitHub Copilot SDK tool orchestration using your server-side model provider; no GitHub subscription required.' },
  { id: 'M6', label: 'M6 · Council',          hint: 'Thorough. Three parallel strategists + verifier + synthesiser (slower).' },
];


export interface Message {
  id:      string;
  role:    Role;
  content: string;
  /** Files the user attached when sending this message. Only present on
   *  user-role messages; rendered as small chips below the bubble text. */
  attachments?: AttachmentSummary[];
  /** Skill that was active when this turn was sent. The skill's prompt
   *  was injected as additional system instructions on the backend; we
   *  only persist the *reference* on the message so the UI can show a
   *  chip ("via Skill: …") above both the user and assistant bubbles. */
  skill?: SkillRef;
  /** Which agent method produced this assistant answer (M4/M6/M7). Only
   *  present on assistant messages — renders as a small badge so the
   *  choice in the settings gear is visibly reflected on each answer. */
  method?: MethodId;
  model?: string;
  agentId?: string;
  grounding?: string;
  requestId?: string;
}

/** Lightweight reference to a Skill, stored on Messages so the UI can
 *  render a chip without rehydrating the full prompt body. */
export interface SkillRef {
  id:            string;
  name:          string;
  businessAreas: string[];
  regions:       string[];
}

/**
 * A document the user has dragged or picked into the composer. The text
 * has already been extracted server-side by /api/upload and is held in
 * memory so we can include it as a context preamble when the user sends
 * the next message.
 */
export interface Attachment {
  documentId?: string;
  documentUrl?: string;
  sourceId?: string;
  /** Local UUID, used for React keys and removal. */
  id:        string;
  /** Distinguishes a text-extracted document from an image we'll send to
   *  the model as `input_image`. Defaults to 'text' for backwards compat. */
  kind:      'text' | 'image';
  /** Original filename. */
  name:      string;
  /** Original file size in bytes. */
  size:      number;
  /** Reported MIME type (best-effort, browsers don't always set one). */
  mime:      string;
  /** Extracted plaintext (UTF-8). May be truncated. Empty for images. */
  text:      string;
  /** For images: a `data:image/...;base64,...` URL we hand to the model
   *  via the Responses API `input_image` content item. Also used in the
   *  composer chip to render a tiny thumbnail. */
  dataUrl?:  string;
  /** True if the extracted text was clamped to fit our per-file budget.
   *  Always false for images. */
  truncated: boolean;
  /** UI status: 'uploading' while POSTing to /api/upload, 'ready' once
   *  text is available, 'error' if extraction failed. */
  status:    'uploading' | 'ready' | 'error';
  /** Human-readable error when status === 'error'. */
  error?:    string;
}

/** What we persist on the user's Message so the chip appears next time the
 *  conversation is re-rendered (or, in future, restored from disk). */
export interface AttachmentSummary {
  documentId?: string;
  documentUrl?: string;
  sourceId?: string;
  id:        string;
  kind:      'text' | 'image';
  name:      string;
  size:      number;
  mime:      string;
  truncated: boolean;
  /** Approx char count of extracted text (so we can show "~ 12 KB of text").
   *  0 for images. */
  textChars: number;
  /** Inline data URL for images, so the user bubble can show a thumbnail
   *  even when the conversation is re-rendered. Omitted for text files. */
  dataUrl?:  string;
}

export interface Conversation {
  id:        string;
  title:     string;
  messages:  Message[];
  createdAt: number;
}

// ─── Skills library ────────────────────────────────────────────────────────
// A "skill" is a saved, reusable prompt that the user can drop into the
// composer (or fire straight off as a new chat). Skills carry two
// orthogonal tag axes — business area and region — so the library can be
// filtered along both dimensions simultaneously (multi-select on each).

/** One saved prompt in the user's skills library. */
export interface Skill {
  /** Local UUID. Stable across renames so we can keep React keys. */
  id:             string;
  /** Short human-friendly name shown in the list. */
  name:           string;
  /** One-line summary surfaced under the name. */
  description:    string;
  /** The actual prompt text that gets dropped into the composer when the
   *  user clicks the skill. Multi-line, markdown allowed. */
  prompt:         string;
  /** Business / discipline tags — e.g. "Water", "Transport", "Digital".
   *  Multi-tag. */
  businessAreas:  string[];
  /** Region / jurisdiction tags — e.g. "UK", "Global". */
  regions:        string[];
  /** Epoch ms; used for default sort. */
  createdAt:      number;
  /** Epoch ms; bumped on every edit. */
  updatedAt:      number;
  /** True for seed entries that ship with the app. Built-ins can't be
   *  deleted (only hidden) so a fresh install always has examples. */
  builtin?:       boolean;
  /** When true, sending this skill auto-enables Work IQ for the turn so
   *  the answer can pull from the user's Microsoft 365 corpus. No-op
   *  unless the user also has a Microsoft 365 Copilot licence. */
  useWorkIQ?:     boolean;
}

/** A single agent activity or answer event received during streaming. */
export interface ProgressEvent {
  /** e.g. 'start', 'assistant_turn', 'tool_start', 'tool_done', 'delta'. */
  phase:   string;
  /** Wall-clock ms when the event was received in the browser. */
  ts:      number;
  /** Phase-specific fields (sql, name, row_count, error, etc.). */
  payload: Record<string, unknown>;
}
