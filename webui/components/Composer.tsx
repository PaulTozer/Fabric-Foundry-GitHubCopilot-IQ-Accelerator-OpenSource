'use client';
import { DocumentLink } from '@/components/DocumentLink';

import {
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
} from 'react';
import type { Attachment } from '@/lib/types';

interface ComposerProps {
  disabled: boolean;
  onSend:   (text: string, attachments: Attachment[]) => void;
}

/** Max attachments per message — keep the prompt budget sane. */
const MAX_ATTACHMENTS = 8;

export function Composer({
  disabled,
  onSend,
}: ComposerProps) {
  const [text, setText]               = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [dragOver, setDragOver]       = useState(false);
  const ref      = useRef<HTMLTextAreaElement | null>(null);
  const fileRef  = useRef<HTMLInputElement  | null>(null);
  // Track nested dragenter / dragleave events so the highlight doesn't
  // flicker when the cursor moves over a child element of the drop zone.
  const dragDepth = useRef(0);

  const ready    = attachments.every((a) => a.status !== 'uploading');
  const canSend  = !disabled && ready && (text.trim().length > 0 || attachments.length > 0);

  function submit(): void {
    if (!canSend) return;
    // Only forward attachments that successfully extracted text. 'error'
    // chips stay in the composer so the user can decide to remove them.
    const ok = attachments.filter((a) => a.status === 'ready');
    onSend(text.trim(), ok);
    setText('');
    setAttachments([]);
    attachmentCount.current = 0;
    requestAnimationFrame(() => ref.current?.focus());
  }

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  // ─── File ingestion ───────────────────────────────────────────────────
  const attachmentCount = useRef(0);
  function ingestFiles(files: FileList | File[]) {
    const incoming = Array.from(files);
    if (incoming.length === 0) return;

      const remaining = Math.max(0, MAX_ATTACHMENTS - attachmentCount.current);
      const toAdd     = incoming.slice(0, remaining);
      const next: Attachment[] = [];
      const uploads: { file: File; id: string }[] = [];
      attachmentCount.current += toAdd.length;

      for (const file of toAdd) {
        const id = crypto.randomUUID();
        // Optimistic: guess `kind` from the browser-reported MIME so the
        // chip can render a thumbnail immediately for images. The server
        // is the source of truth and may override it on response.
        const guessedKind: 'text' | 'image' =
          file.type.startsWith('image/') ? 'image' : 'text';
        const stub: Attachment = {
          id,
          kind:      guessedKind,
          name:      file.name,
          size:      file.size,
          mime:      file.type || '',
          text:      '',
          truncated: false,
          status:    'uploading',
        };
        next.push(stub);
        uploads.push({ file, id });
      }
      setAttachments(prev => [...prev, ...next]);
      uploads.forEach(({ file, id }) => { void uploadOne(file, id); });
  }

  async function uploadOne(file: File, id: string): Promise<void> {
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (!res.ok) {
        const msg = typeof body.error === 'string' ? body.error : `HTTP ${res.status}`;
        setAttachments((cur) =>
          cur.map((a) => a.id === id
            ? { ...a, status: 'error', error: msg }
            : a),
        );
        return;
      }
      setAttachments((cur) =>
        cur.map((a) => a.id === id
          ? {
              ...a,
              status:    'ready',
              kind:      (body.kind === 'image' ? 'image' : 'text') as 'text' | 'image',
              text:      String(body.text ?? ''),
              truncated: Boolean(body.truncated),
              mime:      String(body.mime ?? a.mime),
              dataUrl:   typeof body.dataUrl === 'string' ? body.dataUrl : undefined,
              documentId: typeof body.documentId === 'string' ? body.documentId : undefined,
              documentUrl: typeof body.documentUrl === 'string' ? body.documentUrl : undefined,
              sourceId: typeof body.sourceId === 'string' ? body.sourceId : undefined,
            }
          : a),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setAttachments((cur) =>
        cur.map((a) => a.id === id
          ? { ...a, status: 'error', error: msg }
          : a),
      );
    }
  }

  function removeAttachment(id: string): void {
    attachmentCount.current = Math.max(0, attachmentCount.current - 1);
    const document = attachments.find(a => a.id === id);
    if (document?.documentUrl) {
      void fetch(document.documentUrl, { method: 'DELETE' }).catch(() =>
        setAttachments(cur => cur.map(a => a.id === id
          ? { ...a, status: 'error', error: 'Could not remove server document; it expires in 30 minutes.' } : a)));
    }
    setAttachments((cur) => cur.filter((a) => a.id !== id));
  }

  function onPick(e: ChangeEvent<HTMLInputElement>): void {
    if (e.target.files) ingestFiles(e.target.files);
    // Reset the input so picking the same file twice re-uploads it.
    e.target.value = '';
  }

  // ─── Drag-and-drop ────────────────────────────────────────────────────
  function onDragEnter(e: DragEvent<HTMLDivElement>): void {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragOver(true);
  }

  function onDragOver(e: DragEvent<HTMLDivElement>): void {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }

  function onDragLeave(e: DragEvent<HTMLDivElement>): void {
    if (!hasFiles(e)) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragOver(false);
  }

  function onDrop(e: DragEvent<HTMLDivElement>): void {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth.current = 0;
    setDragOver(false);
    if (disabled) return;
    ingestFiles(e.dataTransfer.files);
  }

  return (
    <div
      className={`relative border-t border-brand-line bg-white transition-colors ${
        dragOver ? 'bg-brand-paper' : ''
      }`}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center border-2 border-dashed border-brand-magenta/60 bg-brand-magenta/5">
          <span className="rounded-md bg-white/90 px-3 py-1.5 text-sm font-medium text-brand-green-dark shadow-xs">
            Drop to attach
          </span>
        </div>
      )}

      <div className="mx-auto w-full max-w-3xl px-4 pt-3">
        {attachments.length > 0 && (
          <ul className="mb-2 flex flex-wrap gap-2" aria-label="Attached files">
            {attachments.map((a) => (
              <li key={a.id}>
                <Chip att={a} onRemove={() => removeAttachment(a.id)} />
              </li>
            ))}
          </ul>
        )}

      </div>

      <div className="mx-auto flex w-full max-w-3xl items-end gap-2 px-4 pb-3">
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          onChange={onPick}
          // Accept: text-like, PDF, DOCX, plus PNG/JPEG/WEBP/GIF images so the
          // model can ingest screenshots, diagrams, etc. via input_image.
          // Browsers vary in how they match these so leave it permissive
          // — the API enforces the real rules.
          accept=".txt,.md,.csv,.tsv,.json,.jsonl,.xml,.html,.htm,.yml,.yaml,.log,.sql,.py,.js,.ts,.tsx,.go,.rs,.rb,.java,.c,.cpp,.h,.hpp,.cs,.pdf,.docx,.png,.jpg,.jpeg,.webp,.gif,text/*,application/pdf,application/json,application/xml,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,image/webp,image/gif"
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={disabled || attachments.length >= MAX_ATTACHMENTS}
          aria-label="Attach files"
          title={
            attachments.length >= MAX_ATTACHMENTS
              ? `Maximum ${MAX_ATTACHMENTS} attachments`
              : 'Attach files (or drag & drop)'
          }
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-brand-line bg-white text-brand-grey/70 shadow-xs transition hover:border-brand-magenta hover:text-brand-green-dark disabled:cursor-not-allowed disabled:opacity-40"
        >
          <PlusIcon />
        </button>

        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKey}
          placeholder={
            disabled
              ? 'Waiting for the previous answer…'
              : attachments.length > 0
                  ? 'Add a question about the attached files…'
                  : 'Ask Example Enterprise IQ — drop a document for context…'
          }
          disabled={disabled}
          className="max-h-48 flex-1 resize-none rounded-xl border border-brand-line bg-brand-paper px-4 py-3 text-sm leading-relaxed inset-shadow-sm outline-hidden transition placeholder:text-brand-grey/40 focus:border-brand-magenta focus:ring-2 focus:ring-brand-magenta/20 disabled:opacity-60"
        />
        <button
          onClick={submit}
          disabled={!canSend}
          className="h-11 rounded-full bg-brand-purple px-6 text-xs font-bold uppercase tracking-wide text-white shadow-xs transition hover:bg-brand-green-dark disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send
        </button>
      </div>
      <p className="flex items-center justify-center gap-1.5 pb-2 text-center text-[11px] text-brand-grey/40">
        <kbd className="rounded-sm border border-brand-line bg-white px-1.5 py-0.5 font-sans text-[10px] text-brand-grey/55">Enter</kbd>
        to send ·
        <kbd className="rounded-sm border border-brand-line bg-white px-1.5 py-0.5 font-sans text-[10px] text-brand-grey/55">Shift+Enter</kbd>
        for a new line · answers may be inaccurate — verify before relying on them.
      </p>
    </div>
  );
}

// ─── Attachment chip ────────────────────────────────────────────────────────

function Chip({ att, onRemove }: { att: Attachment; onRemove: () => void }) {
  const isUploading = att.status === 'uploading';
  const isError     = att.status === 'error';

  let border = 'border-brand-line';
  let bg     = 'bg-white';
  let textC  = 'text-brand-grey';
  if (isError) {
    border = 'border-red-300';
    bg     = 'bg-red-50';
    textC  = 'text-red-700';
  } else if (isUploading) {
    border = 'border-brand-line';
    bg     = 'bg-brand-paper';
    textC  = 'text-brand-grey/70';
  }

  return (
    <div
      className={`group flex max-w-[280px] items-center gap-2 rounded-lg border ${border} ${bg} px-2.5 py-1.5 text-xs ${textC} shadow-xs`}
      title={isError ? att.error : `${att.name} · ${formatBytes(att.size)}${att.truncated ? ' · truncated' : ''}`}
    >
      {att.kind === 'image' && att.dataUrl
        ? <ImageThumb src={att.dataUrl} alt={att.name} />
        : <FileIcon ext={extOf(att.name)} />}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{att.documentUrl
          ? <DocumentLink href={att.documentUrl}>{att.name}</DocumentLink> : att.name}</div>
        <div className="truncate text-[10px] text-brand-grey/50">
          {isUploading
            ? 'Reading…'
            : isError
              ? (att.error ?? 'Failed to read')
              : `${formatBytes(att.size)}${att.truncated ? ' · truncated' : ''}`}
        </div>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${att.name}`}
        className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-brand-grey/40 transition hover:bg-brand-line hover:text-brand-grey"
      >
        ×
      </button>
    </div>
  );
}

// ─── Tiny SVG icons (no extra deps) ────────────────────────────────────────

function PlusIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      width={20}
      height={20}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      aria-hidden
    >
      <path d="M10 4v12M4 10h12" />
    </svg>
  );
}

function FileIcon({ ext }: { ext: string }) {
  // A simple page glyph; the extension label communicates the type.
  const label = (ext || 'doc').slice(0, 4).toUpperCase();
  return (
    <div
      className="grid h-7 w-7 shrink-0 place-items-center rounded-sm bg-brand-grey/10 text-[8px] font-mono font-semibold text-brand-grey/70"
      aria-hidden
    >
      {label}
    </div>
  );
}

function ImageThumb({ src, alt }: { src: string; alt: string }) {
  // Slightly larger than FileIcon so the preview is actually legible.

  return (
    <img
      src={src}
      alt={alt}
      className="h-7 w-7 shrink-0 rounded-sm object-cover"
    />
  );
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function extOf(name: string): string {
  const m = /\.([A-Za-z0-9]+)$/.exec(name);
  return m ? m[1]!.toLowerCase() : '';
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function hasFiles(e: DragEvent<HTMLDivElement>): boolean {
  const dt = e.dataTransfer;
  if (!dt) return false;
  // Both `types` and `items` are populated during dragover — different
  // browsers expose different things, so check both.
  if (dt.types && Array.from(dt.types).includes('Files')) return true;
  if (dt.items) {
    for (let i = 0; i < dt.items.length; i++) {
      if (dt.items[i]?.kind === 'file') return true;
    }
  }
  return false;
}
