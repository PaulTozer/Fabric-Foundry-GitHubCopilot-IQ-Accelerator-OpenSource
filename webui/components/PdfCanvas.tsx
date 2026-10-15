'use client';
import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';

export function PdfCanvas({ url, initialPage }: { url: string; initialPage: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(initialPage);
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    let destroy: (() => Promise<void>) | undefined;
    setError('');
    setPdf(null);
    async function load() {
      const pdfjs = await import('pdfjs-dist');
      if (disposed) return;
      pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';
      const loading = pdfjs.getDocument({ url });
      destroy = () => loading.destroy();
      const document = await loading.promise;
      if (disposed) return;
      setPage(Math.min(Math.max(1, initialPage), document.numPages));
      setPdf(document);
    }
    load().catch(err => { if (!disposed) setError(`PDF could not be loaded: ${err.message}`); });
    return () => {
      disposed = true;
      void destroy?.().catch(() => console.warn('PDF worker cleanup failed.'));
    };
  }, [url, initialPage]);
  useEffect(() => {
    if (!pdf) return;
    const document = pdf;
    let disposed = false;
    let renderTask: RenderTask | undefined;
    setError('');
    async function render() {
      const documentPage = await document.getPage(page);
      const target = canvas.current;
      if (!target || disposed) return;
      const viewport = documentPage.getViewport({ scale: 1.25 });
      target.width = viewport.width;
      target.height = viewport.height;
      renderTask = documentPage.render({ canvas: target, viewport });
      await renderTask.promise;
    }
    render().catch(err => { if (!disposed) setError(`PDF could not be rendered: ${err.message}`); });
    return () => { disposed = true; renderTask?.cancel(); };
  }, [pdf, page]);
  const total = pdf?.numPages ?? 0;
  return <div>
    <nav aria-label="PDF page navigation" className="mb-2 flex items-center gap-3">
      <button type="button" disabled={!total || page <= 1} onClick={() => setPage(p => p - 1)}>Previous page</button>
      <span aria-live="polite">Page {page} / {total || '...'}</span>
      <button type="button" disabled={!total || page >= total} onClick={() => setPage(p => p + 1)}>Next page</button>
    </nav>
    {error && <p role="alert">{error}</p>}
    <div className="max-h-[65vh] overflow-auto">
      <canvas ref={canvas} role="img" aria-label={`PDF page ${page}`} className="max-w-full" />
    </div>
  </div>;
}
