import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deflateRawSync } from 'node:zlib';
import { checkDocxBounds } from '../lib/docx-bounds';
import { unzipSync } from 'fflate';

function archive(entries: { name: string; content: Buffer; declaredSize?: number }[], count = entries.length): Buffer {
  const locals: Buffer[] = [];
  const directory: Buffer[] = [];
  let localOffset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name);
    const compressed = deflateRawSync(entry.content);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(8, 8);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(entry.declaredSize ?? entry.content.length, 22);
    local.writeUInt16LE(name.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(8, 10);
    central.writeUInt32LE(compressed.length, 20);
    central.writeUInt32LE(entry.declaredSize ?? entry.content.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(localOffset, 42);
    locals.push(local, name, compressed);
    directory.push(central, name);
    localOffset += local.length + name.length + compressed.length;
  }
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50);
  end.writeUInt16LE(count, 8);
  end.writeUInt16LE(count, 10);
  end.writeUInt32LE(Buffer.concat(directory).length, 12);
  end.writeUInt32LE(localOffset, 16);
  return Buffer.concat([...locals, ...directory, end]);
}

test('DOCX bounds accept bounded entries and reject falsified inflated sizes', () => {
  const entry = { name: 'word/document.xml', content: Buffer.from('<document>Example</document>') };
  assert.doesNotThrow(() => checkDocxBounds(archive([entry])));
  assert.throws(() => checkDocxBounds(archive([{ ...entry, declaredSize: 1 }])), /decompression/);
  assert.throws(() => checkDocxBounds(archive([{
    name: 'word/document.xml', content: Buffer.alloc(31 * 1024 * 1024, 65), declaredSize: 1,
  }])));
});

test('DOCX bounds reject hidden directory entries, duplicate names and traversal', () => {
  const entry = { name: 'word/document.xml', content: Buffer.from('Example') };
  assert.throws(() => checkDocxBounds(archive([entry, { ...entry, name: 'hidden.xml' }], 1)), /count mismatch/);
  assert.throws(() => checkDocxBounds(archive([entry, entry])), /duplicate/);
  assert.throws(() => checkDocxBounds(archive([{ ...entry, name: '../document.xml' }])), /entry name/);
});

test('embedded ZIP in archive comment never reaches the downstream extractor', () => {
  const outer = archive([{ name: 'word/document.xml', content: Buffer.from('Validated outer document') }]);
  const nested = Buffer.concat([
    archive([{ name: 'word/document.xml', content: Buffer.from('Unvalidated alternate document') }]),
    Buffer.from('extra-comment-byte'),
  ]);
  outer.writeUInt16LE(nested.length, outer.length - 2);
  const normalized = checkDocxBounds(Buffer.concat([outer, nested]));
  assert.equal(Buffer.from(unzipSync(normalized)['word/document.xml']!).toString(), 'Validated outer document');
  assert.doesNotMatch(normalized.toString(), /Unvalidated alternate/);
});
