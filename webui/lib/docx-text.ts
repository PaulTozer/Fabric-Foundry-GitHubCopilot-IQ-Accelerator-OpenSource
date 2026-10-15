import { SaxesParser, type SaxesTagNS } from 'saxes';
import { codePoint as symbolCodePoint } from 'dingbat-to-unicode';
import { unzipSync } from 'fflate';
import { checkDocxBounds } from './docx-bounds';
import { MAX_TEXT_CHARS } from './document-policy';

const WORD_NAMESPACES = new Set([
  'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
  'http://purl.oclc.org/ooxml/wordprocessingml/main',
]);
const COMPATIBILITY_NAMESPACE = 'http://schemas.openxmlformats.org/markup-compatibility/2006';
const MAX_XML_DEPTH = 128;
const MAX_XML_ELEMENTS = 200_000;
type Frame = {
  tag: SaxesTagNS;
  inBody: boolean;
  ignored: boolean;
  omitParagraphEnd?: boolean;
};

function decodeXml(bytes: Uint8Array): string {
  const littleEndian = bytes[0] === 0xff && bytes[1] === 0xfe || bytes[0] === 0x3c && bytes[1] === 0;
  const bigEndian = bytes[0] === 0xfe && bytes[1] === 0xff || bytes[0] === 0 && bytes[1] === 0x3c;
  try {
    return new TextDecoder(littleEndian ? 'utf-16le' : bigEndian ? 'utf-16be' : 'utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new Error('Invalid DOCX XML encoding.');
  }
}

export function extractDocx(input: Buffer): { bytes: Buffer; text: string } {
  const bytes = checkDocxBounds(input);
  const xml = unzipSync(bytes, { filter: entry => entry.name === 'word/document.xml' })['word/document.xml'];
  if (!xml) throw new Error('DOCX missing word/document.xml.');
  const parser = new SaxesParser({ xmlns: true });
  const stack: Frame[] = [];
  const output: string[] = [];
  let elements = 0;
  let characters = 0;
  let namespace = '';
  let bodyFound = false;
  function append(value: string) {
    // Keep one extra character so the upload contract still reports truncation.
    const bounded = value.slice(0, Math.max(0, MAX_TEXT_CHARS + 1 - characters));
    if (bounded) { output.push(bounded); characters += bounded.length; }
  }
  parser.on('error', () => { throw new Error('Invalid DOCX document XML.'); });
  parser.on('doctype', () => { throw new Error('DOCX DTD and entity definitions are not supported.'); });
  parser.on('processinginstruction', () => { throw new Error('DOCX XML processing instructions are not supported.'); });
  parser.on('opentag', tag => {
    if (++elements > MAX_XML_ELEMENTS) throw new Error('DOCX XML element limit exceeded.');
    if (stack.length >= MAX_XML_DEPTH) throw new Error('DOCX XML depth limit exceeded.');
    const parent = stack.at(-1);
    if (!parent) {
      if (tag.local !== 'document' || !WORD_NAMESPACES.has(tag.uri)) throw new Error('Invalid DOCX document root.');
      namespace = tag.uri;
    }
    const word = tag.uri === namespace;
    const body = stack.length === 1 && word && tag.local === 'body';
    if (body) {
      if (bodyFound) throw new Error('DOCX contains multiple document bodies.');
      bodyFound = true;
    }
    const activeDeletion = word && tag.local === 'del' && parent?.inBody
      && !parent.ignored && parent.tag.uri === namespace;
    if (activeDeletion && parent.tag.local === 'rPr'
      && stack.at(-2)?.tag.uri === namespace && stack.at(-2)?.tag.local === 'pPr') {
      const paragraph = stack.findLast(frame => frame.tag.uri === namespace && frame.tag.local === 'p');
      if (paragraph) paragraph.omitParagraphEnd = true;
    }
    if (activeDeletion && parent.tag.local === 'trPr') {
      const row = stack.findLast(frame => frame.tag.uri === namespace && frame.tag.local === 'tr');
      if (row) row.ignored = true;
    }
    const frame: Frame = {
      tag, inBody: body || parent?.inBody === true,
      ignored: parent?.ignored === true || word && ['del', 'moveFrom'].includes(tag.local)
        || tag.uri === COMPATIBILITY_NAMESPACE && tag.local === 'Choice',
    };
    stack.push(frame);
    if (!frame.inBody || frame.ignored || !word) return;
    if (tag.local === 'altChunk') throw new Error('DOCX embedded alternate documents are not supported.');
    if (tag.local === 'tab') append('\t');
    if (tag.local === 'br' || tag.local === 'cr') append('\n');
    if (tag.local === 'noBreakHyphen') append('\u2011');
    if (tag.local === 'softHyphen') append('\u00ad');
    if (tag.local === 'sym') {
      const attribute = (local: string) => Object.values(tag.attributes).find(item => item.uri === namespace && item.local === local)?.value;
      const font = attribute('font');
      const hex = attribute('char');
      if (!font || !hex || !/^[0-9a-f]{1,4}$/i.test(hex)) throw new Error('Invalid DOCX symbol character.');
      const point = Number.parseInt(hex, 16);
      const symbol = symbolCodePoint(font, point >= 0xf000 && point <= 0xf0ff ? point - 0xf000 : point);
      if (!symbol) throw new Error('DOCX contains an unsupported symbol font or character.');
      append(symbol.string);
    }
  });
  const readText = (value: string) => {
    const frame = stack.at(-1);
    if (frame?.inBody && !frame.ignored && frame.tag.uri === namespace && frame.tag.local === 't') append(value);
  };
  parser.on('text', readText);
  parser.on('cdata', readText);
  parser.on('closetag', () => {
    const frame = stack.pop();
    if (frame?.inBody && !frame.ignored && !frame.omitParagraphEnd && frame.tag.uri === namespace && frame.tag.local === 'p') append('\n\n');
  });
  parser.write(decodeXml(xml)).close();
  if (!bodyFound) throw new Error('DOCX missing document body.');
  return { bytes, text: output.join('') };
}
