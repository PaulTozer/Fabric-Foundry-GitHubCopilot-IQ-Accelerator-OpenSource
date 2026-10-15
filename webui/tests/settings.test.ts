import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_SETTINGS, loadSettings, saveSettings } from '../lib/settings-store';
import { METHOD_OPTIONS } from '../lib/types';

test('legacy stage preferences are removed without resetting general settings or other stores', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const stored = new Map<string, string>([
    ['enterpriseiq:settings:v1', JSON.stringify({
      method: 'M6', ontologyEnabled: false, workIQEnabled: true, ribaEnabledStages: [2, 4],
    })],
    ['enterpriseiq:skills:v1', 'saved skills'],
    ['saved-general-chat', 'saved conversation'],
  ]);
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    localStorage: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
    },
  } });
  try {
    const expected = { method: 'M6', ontologyEnabled: false, workIQEnabled: true };
    assert.deepEqual(loadSettings(), expected);
    assert.deepEqual(JSON.parse(stored.get('enterpriseiq:settings:v1')!), expected);
    saveSettings({ method: 'M4', ontologyEnabled: true, workIQEnabled: false });
    assert.deepEqual(JSON.parse(stored.get('enterpriseiq:settings:v1')!), DEFAULT_SETTINGS);
    assert.equal(stored.get('enterpriseiq:skills:v1'), 'saved skills');
    assert.equal(stored.get('saved-general-chat'), 'saved conversation');
  } finally {
    if (original) Object.defineProperty(globalThis, 'window', original);
    else Reflect.deleteProperty(globalThis, 'window');
  }
});

test('SSR settings defaults contain no stage preferences', () => {
  assert.deepEqual(loadSettings(), { method: 'M4', ontologyEnabled: true, workIQEnabled: false });
});

test('visible methods retain SDK and Council but not the legacy M7 path', () => {
  assert.deepEqual(METHOD_OPTIONS.map(option => option.id), ['M4', 'M6']);
});

test('saved M7 migrates persistently without implicit Work IQ consent or changes to account stores', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const stored = new Map<string, string>([
    ['enterpriseiq:agents:v1:account-one', 'saved SDK model, persona and disabled remote server'],
    ['enterpriseiq:agents:v1:account-two', 'other account preferences'],
  ]);
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    localStorage: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
    },
  } });
  try {
    for (const flag of [true, false, undefined]) {
      stored.set('enterpriseiq:settings:v1', JSON.stringify({
        method: 'M7', ontologyEnabled: false, workIQEnabled: flag,
      }));
      const expected = { method: 'M4', ontologyEnabled: false, workIQEnabled: flag === true };
      assert.deepEqual(loadSettings(), expected);
      assert.deepEqual(JSON.parse(stored.get('enterpriseiq:settings:v1')!), expected);
      assert.deepEqual(loadSettings(), expected);
    }
    assert.equal(stored.get('enterpriseiq:agents:v1:account-one'), 'saved SDK model, persona and disabled remote server');
    assert.equal(stored.get('enterpriseiq:agents:v1:account-two'), 'other account preferences');
  } finally {
    if (original) Object.defineProperty(globalThis, 'window', original);
    else Reflect.deleteProperty(globalThis, 'window');
  }
});
