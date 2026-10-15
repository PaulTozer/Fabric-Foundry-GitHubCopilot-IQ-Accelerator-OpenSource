import type { ProgressEvent } from './types';

export const MAX_PROGRESS_EVENTS = 40;

export function progressRequestId(value: unknown): string | undefined {
  return typeof value === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value)
    ? value : undefined;
}

function strategistLabel(payload: Record<string, unknown>): string {
  return typeof payload.strategist === 'string' && /^[ABC]$/.test(payload.strategist)
    ? `Council strategist ${payload.strategist}` : 'Council strategist';
}

function toolIdentifier(payload: Record<string, unknown>): string {
  const name = typeof payload.name === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(payload.name)
    ? payload.name : 'tool';
  const server = typeof payload.server === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(payload.server)
    ? `${payload.server} / ` : '';
  return `${server}${name}`;
}

function toolLabel(payload: Record<string, unknown>): string {
  switch (payload.name) {
    case 'query_ontology': return 'Ontology query';
    case 'query_lakehouse': return 'Lakehouse query';
    case 'schema_card': return 'Schema lookup';
    case 'sample_values': return 'Sample values';
    case 'ask_agent': return 'Sub-agent';
    case 'final_answer': return 'Answer preparation';
    default: return 'Connected tool';
  }
}

export function progressHeadline(event: ProgressEvent): string {
  const p = event.payload;
  switch (event.phase) {
    case 'start': return p.method === 'M6' ? 'Starting Council...'
      : p.method === 'M7' ? 'Starting Work IQ...' : 'Starting Copilot SDK...';
    case 'assistant_turn': return p.iteration === 1
      ? 'Working on your question...' : 'Continuing with the next model step...';
    case 'assistant_activity': return 'Working on the next step...';
    case 'assistant_retry': return 'Retrying the model request...';
    case 'plan': return 'Planning the approach...';
    case 'strategy_start': return `${strategistLabel(p)} is starting...`;
    case 'strategy_tool': return `${strategistLabel(p)}: ${toolLabel(p)} in progress...`;
    case 'strategy_done': return `${strategistLabel(p)} finished a candidate answer.`;
    case 'verifier_start': return 'Council is reviewing candidate answers...';
    case 'verifier_done': return 'Council review completed.';
    case 'synth_start': return 'Council is combining candidate answers...';
    case 'synth_tool': return `Council synthesis: ${toolLabel(p)} in progress...`;
    case 'synth_done': return 'Council synthesis completed.';
    case 'synth_failed': return 'Council synthesis failed; checking available candidates.';
    case 'synth_fallback': return 'Council is using an available candidate answer.';
    case 'tool_start': return `${toolLabel(p)} in progress...`;
    case 'tool_done': return p.error || p.success === false
      ? `${toolLabel(p)} failed. The agent may choose another approach.`
      : `${toolLabel(p)} completed.`;
    case 'delta': return 'Writing the response...';
    case 'skill_applied': return 'Applying the selected skill...';
    case 'critique': return 'Reviewing the response...';
    case 'nudge': return 'Requesting the next model step...';
    case 'final': return 'Preparing the final response...';
    case 'complete': return 'Response complete.';
    case 'cancelled': return 'Response cancelled.';
    case 'failed':
    case 'error': return 'The response could not be completed.';
    default: return 'Agent activity received.';
  }
}

export function progressDetail(event: ProgressEvent): string {
  const p = event.payload;
  if (event.phase === 'start') {
    const label = p.method === 'M4' ? 'Copilot SDK session started.' : 'Agent session started.';
    const requestId = progressRequestId(p.requestId);
    const availability = typeof p.ontologyEnabled === 'boolean'
      && typeof p.ontologyConfigured === 'boolean' && typeof p.ontologyToolAvailable === 'boolean'
      ? ` Ontology requested: ${p.ontologyEnabled ? 'on' : 'off'}; configured: ${p.ontologyConfigured ? 'yes' : 'no'}; tool offered: ${p.ontologyToolAvailable ? 'yes' : 'no'}.`
      : '';
    return `${label}${availability}${requestId ? ` Trace: ${requestId}` : ''}`;
  }
  if (event.phase === 'tool_start' || event.phase === 'tool_done') {
    return `${toolIdentifier(p)}: ${event.phase === 'tool_start' ? 'started'
      : p.error || p.success === false ? 'failed' : 'completed'}`;
  }
  if (event.phase === 'strategy_tool' || event.phase === 'synth_tool') {
    const turn = typeof p.turn === 'number' && Number.isSafeInteger(p.turn) && p.turn > 0
      ? `, step ${p.turn}` : '';
    const actor = event.phase === 'strategy_tool' ? strategistLabel(p) : 'Council synthesis';
    return `${actor}${turn}: ${toolIdentifier(p)} started`;
  }
  if (event.phase === 'assistant_turn' && typeof p.iteration === 'number') {
    return `Model step ${p.iteration} started.`;
  }
  return progressHeadline(event);
}

export function appendProgress(previous: ProgressEvent[], event: ProgressEvent): ProgressEvent[] {
  const delta = previous.find(item => item.phase === 'delta');
  const activity = previous.filter(item => item.phase !== 'delta');
  if (event.phase === 'delta') {
    return [...activity, { ...event, payload: {
      content: String(delta?.payload.content ?? '') + String(event.payload.content ?? ''),
    } }];
  }
  const last = activity.at(-1);
  // Council tool events lack call IDs, so identical names can be distinct calls.
  const uncorrelatedTool = ['strategy_tool', 'synth_tool', 'tool_start', 'tool_done'].includes(event.phase)
    && typeof event.payload.callId !== 'string';
  const repeated = !uncorrelatedTool && last?.phase === event.phase
    && progressDetail(last) === progressDetail(event)
    && last.payload.callId === event.payload.callId;
  const bounded = (repeated ? activity : [...activity, event]).slice(-MAX_PROGRESS_EVENTS);
  // Keep the answer buffer in place until another answer delta arrives.
  return delta ? [delta, ...bounded] : bounded;
}
