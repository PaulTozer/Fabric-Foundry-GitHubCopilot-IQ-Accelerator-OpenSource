'use client';

import { useEffect, useRef, useState } from 'react';
import { assertPreferenceOwner, getWorkspaceAccessToken } from '@/lib/fabric-token';
import { fabricId, validateFabricSelection, type FabricInventory, type FabricSelection,
  type FabricStatus } from '@/lib/fabric-selection';

export interface FabricWorkspaceSettingsProps {
  owner: string;
  selection: FabricSelection | null;
  onChange: (selection: FabricSelection | null, status: FabricStatus | null) => void;
  disabled?: boolean;
}

export function FabricWorkspaceSettings({ owner, selection, onChange, disabled }: FabricWorkspaceSettingsProps) {
  const [workspace, setWorkspace] = useState(selection?.workspaceId ?? '');
  const [inventory, setInventory] = useState<FabricInventory | null>(null);
  const [lakehouse, setLakehouse] = useState('');
  const [ontology, setOntology] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const active = useRef(true);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; controller.current?.abort(); };
  }, []);
  useEffect(() => {
    setWorkspace(selection?.workspaceId ?? '');
    setInventory(null);
  }, [selection?.workspaceId, selection?.lakehouseId, selection?.ontologyId]);

  async function request(body: { workspaceId: string } | FabricSelection): Promise<Response> {
    const requestController = new AbortController();
    controller.current = requestController;
    const token = await getWorkspaceAccessToken('fabric', owner);
    requestController.signal.throwIfAborted();
    return fetch('/api/fabric/workspace', { method: 'POST',
      signal: requestController.signal,
      headers: { 'Content-Type': 'application/json', 'X-Fabric-Token': token },
      body: JSON.stringify(body) });
  }
  async function discover() {
    setBusy(true); setError(''); setMessage(''); setInventory(null);
    try {
      const response = await request({ workspaceId: fabricId(workspace.trim()) });
      const data = await response.json();
      if (!active.current) return;
      if (!response.ok) throw new Error(data.error ?? 'Workspace discovery failed.');
      if (!Array.isArray(data.lakehouses) || !Array.isArray(data.ontologies)) throw new Error('Invalid workspace inventory.');
      const items: FabricInventory = data;
      setInventory(items);
      setLakehouse(items.lakehouses.length === 1 ? items.lakehouses[0]!.id : items.lakehouses.length ? '' : 'none');
      setOntology(items.ontologies.length === 1 ? items.ontologies[0]!.id : items.ontologies.length ? '' : 'none');
      setMessage('Choose the actual items, then validate and save. No rows have been read.');
    } catch (err) { if (active.current) setError(err instanceof Error ? err.message : 'Workspace discovery failed.'); }
    finally { if (active.current) setBusy(false); }
  }
  async function save() {
    setBusy(true); setError(''); setMessage('');
    try {
      const chosen = validateFabricSelection({ workspaceId: fabricId(workspace.trim()),
        lakehouseId: lakehouse === 'none' ? null : lakehouse,
        ontologyId: ontology === 'none' ? null : ontology });
      const response = await request(chosen);
      const data = await response.json();
      if (!active.current) return;
      if (!response.ok) throw new Error(data.error ?? 'Workspace validation failed.');
      if (data.metadataValidated !== true) throw new Error('Workspace metadata was not validated.');
      await assertPreferenceOwner(owner);
      if (!active.current) return;
      const status: FabricStatus = data;
      onChange(validateFabricSelection(status.selection), status);
      setMessage('Saved for this account in this browser. Metadata validated; queries reauthorize per turn. SQL requires separate existing-consented delegated access.');
    } catch (err) { if (active.current) setError(err instanceof Error ? err.message : 'Could not save workspace selection.'); }
    finally { if (active.current) setBusy(false); }
  }
  return <section className="border-t border-brand-line px-4 py-3" aria-label="Fabric workspace settings">
    <label htmlFor="fabric-workspace" className="text-[13px] font-bold">Fabric workspace ID</label>
    <p className="mb-2 text-[11px] text-brand-grey/60">
      Copilot SDK / Council only. Select readable EnterpriseKG items; this does not change the shared backend default.
    </p>
    <input id="fabric-workspace" value={workspace} disabled={disabled || busy}
      onChange={event => { setWorkspace(event.target.value); setInventory(null); setError(''); setMessage('Unsaved edits; chat still uses the saved selection.'); }}
      className="w-full rounded-sm border border-brand-line px-2 py-2 text-xs" placeholder="Workspace UUID" />
    <button type="button" onClick={discover} disabled={disabled || busy || !workspace.trim()}
      className="mt-2 min-h-9 rounded-sm border border-brand-line px-3 text-xs">
      {busy ? 'Validating...' : 'Discover workspace items'}
    </button>
    {inventory && <div className="mt-2 space-y-2">
      <label className="block text-xs">Lakehouse
        <select aria-label="Fabric Lakehouse" value={lakehouse} disabled={disabled || busy}
          onChange={event => setLakehouse(event.target.value)} className="mt-1 w-full rounded-sm border px-2 py-2">
          <option value="">Select an item</option><option value="none">None (ontology only)</option>
          {inventory.lakehouses.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label className="block text-xs">Ontology
        <select aria-label="Fabric Ontology" value={ontology} disabled={disabled || busy}
          onChange={event => setOntology(event.target.value)} className="mt-1 w-full rounded-sm border px-2 py-2">
          <option value="">Select an item</option><option value="none">None (SQL only)</option>
          {inventory.ontologies.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <button type="button" onClick={save} disabled={disabled || busy || !lakehouse || !ontology}
        className="min-h-9 rounded-sm border border-brand-line px-3 text-xs">Validate and save workspace</button>
    </div>}
    {selection && <p className="mt-2 break-all text-[11px]">Saved workspace: {selection.workspaceId}</p>}
    <button type="button" disabled={disabled || busy || !selection} className="mt-2 min-h-9 text-xs underline"
      onClick={() => {
        setBusy(true); setError('');
        void assertPreferenceOwner(owner).then(() => {
          if (!active.current) return;
          onChange(null, null); setWorkspace(''); setInventory(null); setMessage('Cleared. Chat uses the operator default.');
        }).catch(err => {
          if (active.current) setError(err instanceof Error ? err.message : 'Could not clear workspace.');
        }).finally(() => { if (active.current) setBusy(false); });
      }}>Clear workspace / use operator default</button>
    {message && <p role="status" className="mt-2 text-xs">{message}</p>}
    {error && <p role="alert" className="mt-2 text-xs text-red-800">{error}</p>}
  </section>;
}
