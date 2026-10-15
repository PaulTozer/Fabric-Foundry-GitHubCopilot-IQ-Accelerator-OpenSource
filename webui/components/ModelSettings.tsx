'use client';

import { useEffect, useState } from 'react';
import type { EnabledModel, OperatorConfiguration } from '@/lib/agent-config';

export function ModelSettings({ selectedModel, configuration, onChange, disabled }: {
  selectedModel: string | null; configuration: OperatorConfiguration | null;
  onChange: (id: string) => void; disabled: boolean;
}) {
  const [models, setModels] = useState<EnabledModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [draft, setDraft] = useState(selectedModel ?? configuration?.defaultModel ?? '');
  async function refresh(force: boolean, signal?: AbortSignal) {
    setLoading(true); setError('');
    try {
      const response = await fetch(`/api/models${force ? '?refresh=1' : ''}`, { signal, cache: 'no-store' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error ?? 'Enabled model discovery failed.');
      setModels(result.models);
    } catch (err) {
      if (!signal?.aborted) { setModels([]); setError(err instanceof Error ? err.message : 'Enabled model discovery failed.'); }
    } finally { if (!signal?.aborted) setLoading(false); }
  }
  useEffect(() => {
    const controller = new AbortController();
    void refresh(false, controller.signal);
    return () => controller.abort();
  }, []);
  useEffect(() => { setDraft(selectedModel ?? configuration?.defaultModel ?? ''); }, [selectedModel, configuration?.defaultModel]);
  return <section aria-label="AI service configuration" className="space-y-3 border-t border-brand-line px-4 py-3 text-sm">
    <h3 className="font-bold">AI Service Configuration</h3>
    <p>Server-managed provider. Endpoint and credentials are operator-controlled; no browser keys.</p>
    <p className="break-all">Endpoint: {configuration?.endpoint || 'Not configured'}</p>
    <p>Authentication: {configuration?.authentication ?? 'Configuration unavailable'}</p>
    <label className="block font-semibold" htmlFor="enabled-model">Model deployment</label>
    <select id="enabled-model" className="min-h-9 w-full rounded-lg border border-brand-line bg-white p-2"
      value={draft} disabled={loading || disabled || !models.length} onChange={event => { setDraft(event.target.value); setNotice(''); }}>
      {!models.some(model => model.id === draft) && <option value={draft}>{draft ? `${draft} (not in enabled catalog)` : 'Choose enabled deployment'}</option>}
      {models.map(model => <option key={model.id} value={model.id}>{model.id} · {model.model} {model.version}</option>)}
    </select>
    {loading && <p role="status">Loading enabled deployments...</p>}
    {!loading && !error && !models.length && <p role="status">No enabled chat deployments compatible with the configured wire API.</p>}
    {error && <p role="alert" className="text-red-800">{error}</p>}
    {disabled && <p>Model selection and Agent Manager apply only to Copilot SDK BYOK. Council and Work IQ use their separately configured deployments.</p>}
    <div className="flex flex-wrap gap-2">
      <button type="button" className="min-h-9 rounded-sm border px-3 py-1" disabled={loading} onClick={() => void refresh(true)}>Refresh enabled models</button>
      <button type="button" className="min-h-9 rounded-sm border px-3 py-1" disabled={disabled || loading || !models.some(model => model.id === draft)} onClick={() => {
        try { onChange(draft); setNotice('Model saved for this account/browser. Applies to the next SDK turn.'); setError(''); }
        catch (err) { setError(err instanceof Error ? err.message : 'Could not save model selection.'); }
      }}>Save model</button>
      <button type="button" className="min-h-9 rounded-sm border px-3 py-1" onClick={() => { setDraft(selectedModel ?? configuration?.defaultModel ?? ''); setNotice(''); }}>Cancel model change</button>
    </div>
    {notice && <p role="status">{notice}</p>}
    <p>Only enabled deployments discovered on the authorized Foundry resource are listed. Selection is validated again at execution; missing/stale deployments fail explicitly.</p>
  </section>;
}
