import { inflateRawSync } from 'node:zlib';
import { zipSync } from 'fflate';

const MAX_EXPANDED = 30 * 1024 * 1024;
export function checkDocxBounds(bytes: Buffer): Buffer {
  let end = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65_557); i--) {
    if (bytes.readUInt32LE(i) === 0x06054b50 && i + 22 + bytes.readUInt16LE(i + 20) === bytes.length) {
      end = i; break;
    }
  }
  if (end < 0) throw new Error('Invalid DOCX archive.');
  const entries = bytes.readUInt16LE(end + 10);
  const directory = bytes.readUInt32LE(end + 16);
  const directoryEnd = directory + bytes.readUInt32LE(end + 12);
  if (bytes.readUInt16LE(end + 4) || bytes.readUInt16LE(end + 6)
    || bytes.readUInt16LE(end + 8) !== entries || directoryEnd !== end) {
    throw new Error('Unsupported or inconsistent DOCX directory.');
  }
  if (entries > 2000 || entries === 0) throw new Error('DOCX archive entry limit exceeded.');
  let offset = directory;
  let expanded = 0;
  let documentFound = false;
  const names = new Set<string>();
  const validated: [string, Uint8Array][] = [];
  for (let i = 0; i < entries; i++) {
    if (offset + 46 > directoryEnd || bytes.readUInt32LE(offset) !== 0x02014b50) throw new Error('Invalid DOCX directory.');
    const flags = bytes.readUInt16LE(offset + 8);
    const method = bytes.readUInt16LE(offset + 10);
    const compressed = bytes.readUInt32LE(offset + 20);
    const declaredSize = bytes.readUInt32LE(offset + 24);
    const nameLength = bytes.readUInt16LE(offset + 28);
    const next = offset + 46 + nameLength + bytes.readUInt16LE(offset + 30) + bytes.readUInt16LE(offset + 32);
    if (next > directoryEnd || flags & 1 || ![0, 8].includes(method)) throw new Error('Unsupported DOCX entry.');
    const name = bytes.toString('utf8', offset + 46, offset + 46 + nameLength);
    if (names.has(name) || name.startsWith('/') || name.includes('\\') || name.includes('\0') || name.split('/').includes('..')) {
      throw new Error('Invalid or duplicate DOCX entry name.');
    }
    names.add(name);
    if (name === 'word/document.xml') documentFound = true;
    const local = bytes.readUInt32LE(offset + 42);
    if (local + 30 > directory || bytes.readUInt32LE(local) !== 0x04034b50
      || bytes.readUInt16LE(local + 8) !== method || bytes.readUInt16LE(local + 6) !== flags) {
      throw new Error('Invalid DOCX local entry.');
    }
    const localNameLength = bytes.readUInt16LE(local + 26);
    const start = local + 30 + localNameLength + bytes.readUInt16LE(local + 28);
    if (bytes.toString('utf8', local + 30, local + 30 + localNameLength) !== name
      || start + compressed > directory || declaredSize > MAX_EXPANDED - expanded) {
      throw new Error('Invalid or oversized DOCX data.');
    }
    // ZIP size fields are untrusted: bound actual DEFLATE output before XML parsing.
    const data = bytes.subarray(start, start + compressed);
    const actual = method === 0 ? data : inflateRawSync(data, { maxOutputLength: Math.max(1, MAX_EXPANDED - expanded) });
    expanded += actual.length;
    if (actual.length !== declaredSize || expanded > MAX_EXPANDED
      || actual.length / Math.max(1, compressed) > 200) throw new Error('DOCX decompression limit exceeded.');
    validated.push([name, actual]);
    offset = next;
  }
  if (offset !== directoryEnd) throw new Error('DOCX directory count mismatch.');
  if (!documentFound) throw new Error('DOCX missing word/document.xml.');
  // Downstream ZIP readers may select a different EOCD in an archive comment.
  // Pass only normalized, already-inflated entries to the text extractor.
  return Buffer.from(zipSync(Object.fromEntries(validated), { level: 0 }));
}
