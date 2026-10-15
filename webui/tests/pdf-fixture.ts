export function samplePdf(): Buffer {
  const parts = ['%PDF-1.4\n'];
  function stream(page: number): string {
    const content = `BT /F1 18 Tf 30 230 Td (Example PDF page ${page}) Tj ET\n`;
    return `<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}endstream`;
  }
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>',
    stream(1),
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
    stream(2),
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(parts.join('')));
    parts.push(`${index + 1} 0 obj\n${object}\nendobj\n`);
  });
  const xref = Buffer.byteLength(parts.join(''));
  parts.push(`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`);
  offsets.slice(1).forEach(offset => parts.push(`${String(offset).padStart(10, '0')} 00000 n \n`));
  parts.push(`trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return Buffer.from(parts.join(''));
}
