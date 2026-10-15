import { test } from 'node:test';
import assert from 'node:assert/strict';
import { zipSync, unzipSync } from 'fflate';
import { extractDocx } from '../lib/docx-text';
import { MAX_TEXT_CHARS } from '../lib/document-policy';
import { sampleDocx } from './docx-fixture';

test('DOCX preserves paragraphs, table text, hyperlinks, tabs, Unicode and line breaks', () => {
  const bytes = sampleDocx('<w:p><w:r><w:t xml:space="preserve"> One &amp; &#x1F680; </w:t><w:tab/><w:t>two</w:t><w:br/><w:t>three</w:t><w:noBreakHyphen/></w:r></w:p>'
    + '<w:tbl><w:tr><w:tc><w:p><w:hyperlink><w:r><w:t>Cell link</w:t></w:r></w:hyperlink></w:p></w:tc></w:tr></w:tbl>');
  const result = extractDocx(bytes);
  assert.equal(result.text, ' One & \u{1F680} \ttwo\nthree\u2011\n\nCell link\n\n');
  assert.deepEqual(unzipSync(result.bytes)['word/document.xml'], unzipSync(bytes)['word/document.xml']);
});

test('DOCX omits deleted text and field instructions without duplicating alternate markup', () => {
  const result = extractDocx(sampleDocx('<w:p><w:del><w:r><w:t>Deleted</w:t></w:r></w:del>'
    + '<w:moveFrom><w:r><w:t>Moved</w:t></w:r></w:moveFrom><w:ins><w:r><w:t>Kept</w:t></w:r></w:ins>'
    + '<w:r><w:instrText>INCLUDETEXT https://invalid.example</w:instrText></w:r>'
    + '<mc:AlternateContent><mc:Choice Requires="w"><w:r><w:t>Duplicate</w:t></w:r></mc:Choice>'
    + '<mc:Fallback><w:r><w:t> fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>'));
  assert.equal(result.text, 'Kept fallback\n\n');
});

test('DOCX preserves legacy symbol fonts and deleted-paragraph joins but omits deleted rows', () => {
  const body = '<w:p><w:pPr><w:rPr><w:del/></w:rPr></w:pPr><w:r><w:t>Joined </w:t></w:r></w:p>'
    + '<w:p><w:r><w:sym w:font="Wingdings" w:char="F0FC"/></w:r></w:p>'
    + '<w:tbl><w:tr><w:trPr><w:del/></w:trPr><w:tc><w:p><w:r><w:t>Deleted row</w:t></w:r></w:p></w:tc></w:tr></w:tbl>';
  assert.equal(extractDocx(sampleDocx(body)).text, 'Joined \u2713\n\n');
  assert.throws(() => extractDocx(sampleDocx('<w:p><w:r><w:sym w:font="Unsupported" w:char="F0FC"/></w:r></w:p>')), /unsupported symbol/);
  assert.throws(() => extractDocx(sampleDocx('<w:altChunk/>')), /alternate documents/);
});

test('DOCX ignored compatibility choices cannot delete retained fallback rows', () => {
  const body = '<w:tbl><w:tr><mc:AlternateContent>'
    + '<mc:Choice Requires="w"><w:trPr><w:del/></w:trPr></mc:Choice>'
    + '<mc:Fallback><w:trPr/></mc:Fallback></mc:AlternateContent>'
    + '<w:tc><w:p><w:r><w:t>Kept row</w:t></w:r></w:p></w:tc></w:tr></w:tbl>';
  assert.equal(extractDocx(sampleDocx(body)).text, 'Kept row\n\n');
});

test('DOCX ignored compatibility choices cannot join retained paragraphs', () => {
  const body = '<w:p><mc:AlternateContent>'
    + '<mc:Choice Requires="w"><w:pPr><w:rPr><w:del/></w:rPr></w:pPr></mc:Choice>'
    + '<mc:Fallback><w:pPr/></mc:Fallback></mc:AlternateContent><w:r><w:t>First</w:t></w:r></w:p>'
    + '<w:p><w:r><w:t>Second</w:t></w:r></w:p>';
  assert.equal(extractDocx(sampleDocx(body)).text, 'First\n\nSecond\n\n');
});

function xmlArchive(xml: string | Uint8Array): Buffer {
  return Buffer.from(zipSync({ 'word/document.xml': typeof xml === 'string' ? new TextEncoder().encode(xml) : xml }, { level: 0 }));
}

test('DOCX strict WordprocessingML namespaces and UTF-16 remain readable', () => {
  const xml = '<x:document xmlns:x="http://purl.oclc.org/ooxml/wordprocessingml/main"><x:body><x:p><x:r><x:t>Strict text</x:t></x:r></x:p></x:body></x:document>';
  assert.equal(extractDocx(xmlArchive(xml)).text, 'Strict text\n\n');
  assert.equal(extractDocx(xmlArchive(Buffer.concat([Buffer.from([255, 254]), Buffer.from(xml, 'utf16le')]))).text, 'Strict text\n\n');
});

test('DOCX rejects malformed XML, DTD/entities, wrong roots, missing bodies and invalid encoding', () => {
  for (const xml of [
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p></w:body></w:document>',
    '<!DOCTYPE w:document [<!ENTITY data SYSTEM "file:///unavailable">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>&data;</w:body></w:document>',
    '<!DOCTYPE document SYSTEM "https://invalid.example/dtd"><document/>',
    '<document><body><p>Wrong namespace</p></body></document>',
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
  ]) assert.throws(() => extractDocx(xmlArchive(xml)), /DOCX/);
  assert.throws(() => extractDocx(xmlArchive(new Uint8Array([0xff, 0xfe, 0x01]))), /DOCX/);
});

test('DOCX XML depth and element limits accept the exact boundary and reject one beyond it', () => {
  const nested = (count: number) => '<w:p>' + '<w:r>'.repeat(count)
    + '<w:t>bounded</w:t>' + '</w:r>'.repeat(count) + '</w:p>';
  assert.equal(extractDocx(sampleDocx(nested(124))).text, 'bounded\n\n');
  assert.throws(() => extractDocx(sampleDocx(nested(125))), /DOCX.*depth/i);
  assert.equal(extractDocx(sampleDocx('<w:r/>'.repeat(199_998))).text, '');
  assert.throws(() => extractDocx(sampleDocx('<w:r/>'.repeat(199_999))), /DOCX.*element/i);
});

test('DOCX retains a truncation sentinel while validating XML after the output limit', () => {
  const body = `<w:p><w:r><w:t>${'a'.repeat(MAX_TEXT_CHARS + 2)}</w:t></w:r></w:p>`;
  const result = extractDocx(sampleDocx(body));
  assert.equal(result.text.length, MAX_TEXT_CHARS + 1);
  assert.throws(() => extractDocx(sampleDocx(body + '<w:p>')), /DOCX/);
});
