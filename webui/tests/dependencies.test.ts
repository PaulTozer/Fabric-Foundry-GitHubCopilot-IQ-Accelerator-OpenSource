import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

type LockedPackage = {
  version?: string;
  resolved?: string;
  integrity?: string;
  link?: boolean;
  inBundle?: boolean;
  bundleDependencies?: string[];
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
  engines?: Record<string, string>;
};
const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8')) as {
  lockfileVersion: number;
  packages: Record<string, LockedPackage>;
};
const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')) as LockedPackage;

test('dependency lock is registry-independent with integrity for every installed package', () => {
  assert.equal(lock.lockfileVersion, 3);
  assert.ok(Object.keys(lock.packages).length > 1);
  for (const [location, entry] of Object.entries(lock.packages)) {
    if (!location) continue;
    assert.match(location, /^node_modules\//);
    assert.equal(entry.link, undefined, `${location}: local links are not portable`);
    assert.equal(entry.resolved, undefined, `${location}: registry endpoints belong in local npm configuration`);
    assert.match(entry.version ?? '', /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/, `${location}: missing exact version`);
    if (entry.inBundle) {
      let container = location.slice(0, location.lastIndexOf('/node_modules/'));
      while (lock.packages[container]?.inBundle) container = container.slice(0, container.lastIndexOf('/node_modules/'));
      const parent = lock.packages[container];
      assert.ok(parent?.integrity, `${location}: bundled entry needs an integrity-anchored distribution`);
      const relative = location.slice(container.length + '/node_modules/'.length);
      const anchor = relative.split('/').slice(0, relative.startsWith('@') ? 2 : 1).join('/');
      assert.ok(parent.bundleDependencies?.includes(anchor), `${location}: parent must declare its bundled dependency`);
      if (!entry.integrity) continue;
    }
    const integrity = /^sha512-([A-Za-z0-9+/]+={0,2})$/.exec(entry.integrity ?? '');
    assert.ok(integrity, `${location}: missing strong package integrity`);
    const digest = Buffer.from(integrity[1], 'base64');
    assert.equal(digest.length, 64, `${location}: incomplete SHA-512 digest`);
    assert.equal(digest.toString('base64'), integrity[1], `${location}: invalid SHA-512 encoding`);
  }
});

test('locked root matches the supported manifest and public installation configuration', () => {
  const root = lock.packages[''];
  assert.deepEqual(root.dependencies, manifest.dependencies);
  assert.deepEqual(root.devDependencies, manifest.devDependencies);
  assert.deepEqual(root.engines, manifest.engines);
  const config = readFileSync(new URL('../.npmrc', import.meta.url), 'utf8');
  const settings = new Map<string, string>();
  for (const line of config.split(/\r?\n/).map(value => value.trim()).filter(value => value && !/^[#;]/.test(value))) {
    const separator = line.indexOf('=');
    assert.ok(separator > 0, 'npm configuration must use explicit key=value settings');
    const key = line.slice(0, separator).trim().toLowerCase();
    assert.ok(!settings.has(key), `duplicate npm setting: ${key}`);
    settings.set(key, line.slice(separator + 1).trim());
  }
  assert.equal(settings.get('registry'), 'https://registry.npmjs.org/');
  assert.equal(settings.get('omit-lockfile-registry-resolved'), 'true');
  assert.equal(settings.get('min-release-age'), '7');
  assert.doesNotMatch(config, /(?:_auth|token|password|strict-ssl=false)/i);
});
