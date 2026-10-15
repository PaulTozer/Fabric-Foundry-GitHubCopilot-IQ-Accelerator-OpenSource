import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendProgress, MAX_PROGRESS_EVENTS, progressDetail, progressHeadline, progressRequestId } from '../lib/query-progress';
import type { ProgressEvent } from '../lib/types';

const event = (phase: string, payload: Record<string, unknown> = {}): ProgressEvent =>
  ({ phase, payload, ts: 1 });

test('public start naming and statuses never dump protocol IDs or sensitive payloads', () => {
  const start = event('start', { method: 'M4', model: 'deployment', authorization: 'private' });
  assert.equal(progressHeadline(start), 'Starting Copilot SDK...');
  assert.equal(progressDetail(start), 'Copilot SDK session started.');
  for (const phase of ['assistant_activity', 'assistant_retry', 'unknown']) {
    const item = event(phase, { intent: 'hidden', reason: 'private', arguments: 'private' });
    assert.doesNotMatch(progressHeadline(item) + progressDetail(item), /hidden|private|M4/);
  }
});

test('tool failure does not claim retry, access or returned rows; fallback is shown only when called', () => {
  const failed = event('tool_done', { name: 'query_ontology', success: false, error: 'Authorization: private' });
  assert.equal(progressHeadline(failed), 'Ontology query failed. The agent may choose another approach.');
  assert.equal(progressDetail(failed), 'query_ontology: failed');
  assert.equal(progressHeadline(event('tool_start', { name: 'query_lakehouse' })), 'Lakehouse query in progress...');
  assert.equal(progressHeadline(event('tool_done', { name: 'query_lakehouse', success: true })), 'Lakehouse query completed.');
  assert.equal(progressDetail(event('tool_start', { name: 'bad\nprivate', server: 'https://private' })), 'tool: started');
});

test('bounded deduped activity preserves every answer delta and distinct tool calls', () => {
  let items = appendProgress([], event('delta', { content: 'Answer ' }));
  for (let iteration = 1; iteration <= 100; iteration++) {
    const step = event('assistant_turn', { iteration });
    items = appendProgress(items, step);
    items = appendProgress(items, step);
  }
  assert.equal(items.length, MAX_PROGRESS_EVENTS + 1);
  items = appendProgress(items, event('delta', { content: 'continues.' }));
  assert.equal(items.at(-1)?.payload.content, 'Answer continues.');
  items = appendProgress(items, event('tool_start', { name: 'query_ontology', callId: 'one' }));
  items = appendProgress(items, event('tool_start', { name: 'query_ontology', callId: 'two' }));
  assert.equal(items.filter(item => item.phase === 'tool_start').length, 2);
  assert.equal(items.find(item => item.phase === 'delta')?.payload.content, 'Answer continues.');
});

test('terminal and legacy Council statuses are explicit without raw diagnostics', () => {
  for (const [phase, text] of [
    ['complete', 'Response complete.'], ['cancelled', 'Response cancelled.'],
    ['error', 'The response could not be completed.'],
    ['final', 'Preparing the final response...'], ['delta', 'Writing the response...'],
  ]) assert.equal(progressHeadline(event(phase)), text);
  assert.equal(progressHeadline(event('critique', { reason: 'private' })), 'Reviewing the response...');
});

test('actual Council phases retain strategist/tool identity without reasoning or error payloads', () => {
  let items: ProgressEvent[] = [];
  for (const [strategist, turn, name] of [
    ['A', 1, 'query_ontology'], ['B', 1, 'query_lakehouse'], ['B', 2, 'query_lakehouse'],
    ['B', 2, 'query_lakehouse'],
  ]) {
    items = appendProgress(items, event('strategy_tool', {
      strategist, turn, name, sql: 'private', purpose: 'private', question: 'private',
    }));
  }
  assert.equal(items.length, 4);
  assert.equal(progressDetail(items[0]!), 'Council strategist A, step 1: query_ontology started');
  assert.equal(progressHeadline(items[1]!), 'Council strategist B: Lakehouse query in progress...');
  for (const phase of ['strategy_start', 'strategy_done', 'verifier_start', 'verifier_done',
    'synth_start', 'synth_tool', 'synth_done', 'synth_failed', 'synth_fallback']) {
    const item = event(phase, { strategist: 'A', name: 'schema_card', error: 'private', reason: 'private' });
    assert.doesNotMatch(progressHeadline(item) + progressDetail(item), /private|Agent activity received/);
  }
});

test('technical trace distinguishes requested/configured/offered and accepts only opaque request IDs', () => {
  const requestId = 'cadf660b-e251-42ca-a827-4bf2d2650443';
  assert.equal(progressRequestId(requestId), requestId);
  assert.equal(progressRequestId('Authorization: private'), undefined);
  const text = progressDetail(event('start', { method: 'M4', requestId,
    ontologyEnabled: false, ontologyConfigured: true, ontologyToolAvailable: false }));
  assert.match(text, /Ontology requested: off; configured: yes; tool offered: no/);
  assert.match(text, new RegExp(`Trace: ${requestId}`));
  assert.doesNotMatch(text, /M4/);
});
