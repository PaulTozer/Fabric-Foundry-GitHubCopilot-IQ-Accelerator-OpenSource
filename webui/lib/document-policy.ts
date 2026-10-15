export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_TEXT_CHARS = 100_000;
const types: Record<string, string[]> = {
  pdf: ['application/pdf'],
  docx: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  txt: ['text/plain'], md: ['text/markdown', 'text/plain'], markdown: ['text/markdown', 'text/plain'],
  csv: ['text/csv', 'application/vnd.ms-excel', 'text/plain'], json: ['application/json', 'text/plain'],
  png: ['image/png'], jpg: ['image/jpeg'], jpeg: ['image/jpeg'], webp: ['image/webp'], gif: ['image/gif'],
};
export function validateUpload(name: string, mime: string, size: number, bytes: Uint8Array): { ext: string; mime: string; image: boolean } {
  if (!name || name.length > 180 || /[/\\\u0000-\u001f]/.test(name) || name.includes('..')) {
    throw new Error('Invalid filename.');
  }
  if (size <= 0 || size > MAX_FILE_BYTES) throw new Error('Upload must be between 1 byte and 10 MB.');
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  const accepted = types[ext];
  if (!accepted || (mime && mime !== 'application/octet-stream' && !accepted.includes(mime))) {
    throw new Error('Unsupported or mismatched MIME/extension. Use PDF, DOCX, TXT, Markdown, CSV, JSON or images.');
  }
  const kind = accepted[0]!;
  const head = Buffer.from(bytes.subarray(0, 12));
  if (ext === 'pdf' && !head.toString('ascii').startsWith('%PDF-')) throw new Error('Invalid PDF signature.');
  if (ext === 'docx' && !(head[0] === 0x50 && head[1] === 0x4b)) throw new Error('Invalid DOCX signature.');
  const image = kind.startsWith('image/');
  if (image) {
    if (size > 4 * 1024 * 1024) throw new Error('Images must not exceed 4 MB.');
    const valid = kind === 'image/png' ? head.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
      : kind === 'image/jpeg' ? head[0] === 255 && head[1] === 216 && head[2] === 255
      : kind === 'image/gif' ? /^GIF8[79]a/.test(head.toString('ascii'))
      : head.toString('ascii', 0, 4) === 'RIFF' && head.toString('ascii', 8, 12) === 'WEBP';
    if (!valid) throw new Error('Invalid image signature.');
  } else if (!['pdf', 'docx'].includes(ext) && bytes.includes(0)) {
    throw new Error('Binary content is not a text document.');
  }
  return { ext, mime: kind, image };
}

export function documentReference(href: string): { id: string; page: number } | null {
  // Only local opaque IDs. No file paths, data URLs, remote URL proxy or traversal.
  const match = /^\/api\/documents\/([a-zA-Z0-9_-]{1,80})(?:\?page=([1-9][0-9]{0,4}))?(?:#page=([1-9][0-9]{0,4}))?$/.exec(href);
  return match ? { id: match[1]!, page: Number(match[2] ?? match[3] ?? 1) } : null;
}

export function safeMarkdownUrl(url: string): string {
  if (documentReference(url)) return url;
  if (/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(url) && url.length < 2_000_000) return url;
  try {
    const parsed = new URL(url);
    if (['https:', 'mailto:'].includes(parsed.protocol) && !parsed.username && !parsed.password) return url;
  } catch { /* Non-absolute links are not trusted document sources. */ }
  return '';
}
