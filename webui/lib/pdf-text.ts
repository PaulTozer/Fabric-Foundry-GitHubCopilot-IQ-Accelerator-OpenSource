import { MAX_TEXT_CHARS } from '@/lib/document-policy';

export async function extractPdfText(bytes: Buffer): Promise<string> {
  const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
  const loading = pdfjs.getDocument({ data: new Uint8Array(bytes), useSystemFonts: true });
  try {
    const document = await loading.promise;
    if (document.numPages > 200) throw new Error('PDF page limit exceeded (200).');
    let text = '';
    for (let page = 1; page <= document.numPages && text.length <= MAX_TEXT_CHARS; page++) {
      const content = await (await document.getPage(page)).getTextContent();
      text += content.items.map(item => 'str' in item ? item.str : '').join(' ') + '\n';
    }
    return text;
  } finally { await loading.destroy(); }
}
