'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import { PdfCanvas } from '@/components/PdfCanvas';
import { documentReference, safeMarkdownUrl } from '@/lib/document-policy';

interface DocumentData { name: string; mime: string; sourceId: string; text: string; url: string }
export function DocumentLink({ href, children }: { href: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [doc, setDoc] = useState<DocumentData | null>(null);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const reference = documentReference(href);
  const sourceId = reference?.id;
  useEffect(() => {
    if (!open || !sourceId) return;
    const abort = new AbortController();
    const panel = dialog.current;
    const button = trigger.current;
    panel?.showModal();
    fetch(`/api/documents/${sourceId}?metadata=1`, { signal: abort.signal })
      .then(async response => {
        if (!response.ok) throw new Error('Document unavailable, expired or not authorized.');
        setDoc(await response.json());
      }).catch(err => { if (!abort.signal.aborted) setError(err.message); });
    return () => { abort.abort(); panel?.close(); button?.focus(); };
  }, [open, sourceId]);
  if (!reference) {
    const url = safeMarkdownUrl(href);
    return url ? <a href={url} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
  }
  return <>
    <button ref={trigger} type="button" className="text-brand-purple underline"
      onClick={() => { setDoc(null); setError(''); setOpen(true); }}>{children}</button>
    {open && <dialog ref={dialog} aria-label="Document viewer" onCancel={() => setOpen(false)}
      onClose={() => setOpen(false)} className="w-[min(95vw,1000px)] max-h-[90vh] rounded-xl p-5 backdrop:bg-black/50">
      <header className="mb-3 flex items-center justify-between gap-3">
        <div><h2 className="font-bold">{doc?.name ?? 'Document viewer'}</h2>
          <p className="text-xs">Source: {doc?.sourceId ?? reference.id} · Page {reference.page}</p></div>
        <button type="button" autoFocus onClick={() => setOpen(false)} aria-label="Close document viewer">Close</button>
      </header>
      {error && <p role="alert">{error}</p>}
      {!doc && !error && <p role="status">Loading document...</p>}
      {doc?.mime === 'application/pdf'
        ? <PdfCanvas url={doc.url} initialPage={reference.page} />
        : doc && <div className="max-h-[65vh] overflow-auto whitespace-pre-wrap">
          {doc.mime === 'text/markdown' ? <ReactMarkdown skipHtml urlTransform={safeMarkdownUrl}>{doc.text}</ReactMarkdown>
            : <pre className="whitespace-pre-wrap">{doc.text}</pre>}
        </div>}
      {doc && <a className="mt-3 inline-block underline" href={doc.url} download={doc.name}>Download source</a>}
    </dialog>}
  </>;
}
