'use client';

import { useEffect, useMemo, useState } from 'react';
import type { Skill } from '@/lib/types';

interface SkillEditorProps {
  /** When provided, the form is pre-populated; null/undefined means "new". */
  skill?:        Skill | null;
  /** Tag suggestions to surface as click-to-add chips. Pulled from the
   *  current library so the user gets autocomplete-style help without
   *  needing a typeahead component. */
  allBusinessAreas: string[];
  allRegions:       string[];
  /** Full skills library — used to drive the "Import from existing"
   *  picker on the New skill form so the user can clone a built-in or
   *  user skill and tweak it ("make a delta") rather than starting
   *  from a blank page. Ignored when editing or viewing. */
  allSkills?:    Skill[];
  /** Called with the new/edited skill on save. */
  onSave:        (s: Skill) => void;
  /** Close the modal without saving. */
  onCancel:      () => void;
  /** If true the form is read-only (used for built-in seed skills). */
  readOnly?:     boolean;
}

/**
 * Modal dialog for creating / editing a Skill.
 *
 * Tag inputs accept comma- or Enter-separated values and also expose
 * click-to-add chips for existing tag values. The form never auto-closes
 * on outside click — that would lose typed prompts; the user must
 * explicitly Save or Cancel.
 */
export function SkillEditor({
  skill,
  allBusinessAreas,
  allRegions,
  allSkills = [],
  onSave,
  onCancel,
  readOnly = false,
}: SkillEditorProps) {
  const isNew = !skill;
  const [name,         setName]         = useState(skill?.name ?? '');
  const [description,  setDescription]  = useState(skill?.description ?? '');
  const [prompt,       setPrompt]       = useState(skill?.prompt ?? '');
  const [areas,        setAreas]        = useState<string[]>(skill?.businessAreas ?? []);
  const [regions,      setRegions]      = useState<string[]>(skill?.regions ?? []);
  const [useWorkIQ,    setUseWorkIQ]    = useState<boolean>(skill?.useWorkIQ === true);
  const [areaDraft,    setAreaDraft]    = useState('');
  const [regionDraft,  setRegionDraft]  = useState('');
  const [error,        setError]        = useState<string | null>(null);
  // Source-of-truth for the "Import from existing" dropdown. Empty
  // string means "don't import". We don't lock this to a single shot —
  // changing the selection re-seeds the form so the user can A/B between
  // candidates before committing.
  const [importFromId, setImportFromId] = useState<string>('');

  // Re-prime form state if the parent swaps the target skill out from
  // under us without unmounting the modal (e.g. switching from edit
  // to a fresh "new").
  useEffect(() => {
    setName(skill?.name ?? '');
    setDescription(skill?.description ?? '');
    setPrompt(skill?.prompt ?? '');
    setAreas(skill?.businessAreas ?? []);
    setRegions(skill?.regions ?? []);
    setUseWorkIQ(skill?.useWorkIQ === true);
    setAreaDraft('');
    setRegionDraft('');
    setError(null);
    setImportFromId('');
  }, [skill]);

  // Sort + group the import options so the dropdown reads sensibly:
  // built-ins first (alphabetical), then user skills (alphabetical).
  // Built-ins are typically what people want as a starting point.
  const importOptions = useMemo(() => {
    const built  = allSkills.filter((s) => s.builtin).slice()
      .sort((a, b) => a.name.localeCompare(b.name));
    const user   = allSkills.filter((s) => !s.builtin).slice()
      .sort((a, b) => a.name.localeCompare(b.name));
    return { built, user };
  }, [allSkills]);

  // When the user picks an import source, hydrate the form from it.
  // Name gets a "Copy of" prefix so they can immediately see this is a
  // delta and so it doesn't collide with the source skill in the
  // sidebar list (which keys on name visually).
  function applyImport(sourceId: string): void {
    setImportFromId(sourceId);
    if (!sourceId) return;
    const src = allSkills.find((s) => s.id === sourceId);
    if (!src) return;
    // Only auto-prefix when the name field is empty or still equals a
    // previously imported "Copy of …" name — don't clobber something
    // the user has already typed by hand.
    const looksAutoNamed = name === '' || /^Copy of /.test(name);
    if (looksAutoNamed) setName(`Copy of ${src.name}`);
    setDescription(src.description);
    setPrompt(src.prompt);
    setAreas([...src.businessAreas]);
    setRegions([...src.regions]);
    setUseWorkIQ(src.useWorkIQ === true);
  }

  // Suggestions = existing tags that aren't already selected.
  const areaSuggestions = useMemo(
    () => allBusinessAreas.filter((a) => !areas.includes(a)),
    [allBusinessAreas, areas],
  );
  const regionSuggestions = useMemo(
    () => allRegions.filter((r) => !regions.includes(r)),
    [allRegions, regions],
  );

  function addTag(
    raw: string,
    list: string[],
    setList: (v: string[]) => void,
    setDraft: (v: string) => void,
  ): void {
    const cleaned = raw.trim().replace(/,+$/, '').trim();
    if (!cleaned) return;
    if (!list.includes(cleaned)) setList([...list, cleaned]);
    setDraft('');
  }

  function removeTag(value: string, list: string[], setList: (v: string[]) => void): void {
    setList(list.filter((x) => x !== value));
  }

  function handleSubmit(): void {
    if (readOnly) {
      onCancel();
      return;
    }
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    if (!prompt.trim()) {
      setError('Prompt is required.');
      return;
    }
    const now = Date.now();
    const saved: Skill = {
      id:            skill?.id ?? crypto.randomUUID(),
      name:          name.trim(),
      description:   description.trim(),
      prompt:        prompt.trim(),
      businessAreas: dedupe(areas),
      regions:       dedupe(regions),
      createdAt:     skill?.createdAt ?? now,
      updatedAt:     now,
      builtin:       skill?.builtin,
      useWorkIQ:     useWorkIQ ? true : undefined,
    };
    onSave(saved);
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isNew ? 'New skill' : `Edit skill: ${skill?.name}`}
      className="fixed inset-0 z-40 flex items-center justify-center bg-brand-grey/40 p-4"
    >
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <header className="flex items-start justify-between border-b border-brand-line px-5 py-3">
          <div>
            <h2 className="text-base font-semibold text-brand-grey">
              {readOnly ? 'View skill' : isNew ? 'New skill' : 'Edit skill'}
            </h2>
            {readOnly && (
              <p className="mt-0.5 text-xs text-brand-grey/60">
                Built-in skills are read-only. Use the prompt as a starting
                point by clicking <strong>Use</strong> and then editing in the composer.
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onCancel}
            aria-label="Close"
            className="rounded-sm p-1 text-brand-grey/60 hover:bg-brand-paper hover:text-brand-grey"
          >
            ×
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {/* Import-from-existing picker. Only useful on the New form —
              when editing, the form is already populated with the target
              skill, and re-importing would silently overwrite the
              user's in-progress edits. */}
          {isNew && !readOnly && allSkills.length > 0 && (
            <div className="mb-4 rounded-md border border-brand-line bg-brand-paper px-3 py-2.5">
              <label className="block">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-brand-grey/70">
                    Import from existing
                  </span>
                  {importFromId && (
                    <button
                      type="button"
                      onClick={() => applyImport('')}
                      className="text-[11px] text-brand-purple hover:underline"
                    >
                      Clear template
                    </button>
                  )}
                </div>
                <select
                  value={importFromId}
                  onChange={(e) => applyImport(e.target.value)}
                  className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm outline-hidden focus:border-brand-purple focus:ring-2 focus:ring-brand-purple/20"
                >
                  <option value="">— Start from blank —</option>
                  {importOptions.built.length > 0 && (
                    <optgroup label="Built-in skills">
                      {importOptions.built.map((s) => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                      ))}
                    </optgroup>
                  )}
                  {importOptions.user.length > 0 && (
                    <optgroup label="Your skills">
                      {importOptions.user.map((s) => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                      ))}
                    </optgroup>
                  )}
                </select>
                <p className="mt-1.5 text-[11px] text-brand-grey/60">
                  Pick an existing skill to use as a starting point. The
                  prompt, description and tags get copied in so you can
                  tweak them — saving creates a new skill (the original
                  is untouched).
                </p>
              </label>
            </div>
          )}

          <Field label="Name" required>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={readOnly}
              placeholder="e.g. Bid Qualification — Transport"
              className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm outline-hidden focus:border-brand-purple focus:ring-2 focus:ring-brand-purple/20 disabled:bg-brand-paper disabled:text-brand-grey/70"
            />
          </Field>

          <Field label="Description">
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={readOnly}
              placeholder="One-line summary shown in the sidebar"
              className="w-full rounded-md border border-brand-line bg-white px-3 py-2 text-sm outline-hidden focus:border-brand-purple focus:ring-2 focus:ring-brand-purple/20 disabled:bg-brand-paper disabled:text-brand-grey/70"
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <TagField
              label="Business area"
              tags={areas}
              draft={areaDraft}
              setDraft={setAreaDraft}
              suggestions={areaSuggestions}
              disabled={readOnly}
              onAdd={(v) => addTag(v, areas, setAreas, setAreaDraft)}
              onRemove={(v) => removeTag(v, areas, setAreas)}
            />
            <TagField
              label="Region"
              tags={regions}
              draft={regionDraft}
              setDraft={setRegionDraft}
              suggestions={regionSuggestions}
              disabled={readOnly}
              onAdd={(v) => addTag(v, regions, setRegions, setRegionDraft)}
              onRemove={(v) => removeTag(v, regions, setRegions)}
            />
          </div>

          <Field label="Prompt" required>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={readOnly}
              rows={10}
              placeholder="The prompt text that gets dropped into the composer when you click Use."
              className="w-full rounded-md border border-brand-line bg-white px-3 py-2 font-mono text-xs leading-relaxed outline-hidden focus:border-brand-purple focus:ring-2 focus:ring-brand-purple/20 disabled:bg-brand-paper disabled:text-brand-grey/70"
            />
          </Field>

          {/* Work IQ opt-in. Pure metadata on the skill; the page
              hook reads `skill.useWorkIQ` and switches AI-token
              acquisition + workiq_enabled on for the turn. */}
          <Field label="Microsoft 365 grounding">
            <label className="flex items-start gap-2 rounded-sm border border-brand-line bg-brand-paper/40 px-3 py-2 text-xs leading-relaxed text-brand-grey/80">
              <input
                type="checkbox"
                checked={useWorkIQ}
                onChange={(e) => setUseWorkIQ(e.target.checked)}
                disabled={readOnly}
                className="mt-0.5 h-3.5 w-3.5 accent-brand-purple"
              />
              <span>
                <span className="font-medium text-brand-grey">Use Work IQ when this skill is active.</span>{' '}
                When checked, sending the skill turns on the Work IQ tool for the turn so the answer can pull from your Teams chats, Outlook mail, SharePoint and OneDrive. Requires a Microsoft 365 Copilot licence; no-op without one.
              </span>
            </label>
          </Field>

          {error && (
            <p className="mt-2 rounded-sm border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {error}
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-brand-line bg-brand-paper px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-brand-line bg-white px-3 py-1.5 text-sm text-brand-grey hover:border-brand-purple hover:text-brand-purple"
          >
            {readOnly ? 'Close' : 'Cancel'}
          </button>
          {!readOnly && (
            <button
              type="button"
              onClick={handleSubmit}
              className="rounded-md bg-brand-purple px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-green-dark"
            >
              {isNew ? 'Create skill' : 'Save changes'}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="mb-3 block">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-brand-grey/70">
        {label}
        {required && <span className="ml-0.5 text-brand-purple">*</span>}
      </div>
      {children}
    </label>
  );
}

interface TagFieldProps {
  label:       string;
  tags:        string[];
  draft:       string;
  setDraft:    (v: string) => void;
  suggestions: string[];
  disabled:    boolean;
  onAdd:       (v: string) => void;
  onRemove:    (v: string) => void;
}

function TagField({
  label,
  tags,
  draft,
  setDraft,
  suggestions,
  disabled,
  onAdd,
  onRemove,
}: TagFieldProps) {
  return (
    <div className="mb-3">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-brand-grey/70">
        {label}
      </div>
      <div className="rounded-md border border-brand-line bg-white p-1.5 focus-within:border-brand-purple focus-within:ring-2 focus-within:ring-brand-purple/20">
        <div className="flex flex-wrap gap-1">
          {tags.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 rounded-full border border-brand-purple/30 bg-brand-purple/5 px-2 py-0.5 text-[11px] text-brand-purple"
            >
              {t}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => onRemove(t)}
                  aria-label={`Remove ${t}`}
                  className="text-brand-purple/70 hover:text-brand-purple"
                >
                  ×
                </button>
              )}
            </span>
          ))}
          {!disabled && (
            <input
              type="text"
              value={draft}
              onChange={(e) => {
                const v = e.target.value;
                if (v.endsWith(',')) onAdd(v);
                else setDraft(v);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  onAdd(draft);
                } else if (e.key === 'Backspace' && draft === '' && tags.length > 0) {
                  onRemove(tags[tags.length - 1]!);
                }
              }}
              onBlur={() => { if (draft.trim()) onAdd(draft); }}
              placeholder={tags.length === 0 ? 'add tag…' : ''}
              className="min-w-[80px] flex-1 bg-transparent px-1 text-xs outline-hidden"
            />
          )}
        </div>
      </div>

      {!disabled && suggestions.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          <span className="text-[10px] text-brand-grey/40">suggested:</span>
          {suggestions.slice(0, 8).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onAdd(s)}
              className="rounded-full border border-brand-line bg-white px-1.5 py-0.5 text-[10px] text-brand-grey/60 hover:border-brand-purple hover:text-brand-purple"
            >
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function dedupe(arr: string[]): string[] {
  return Array.from(new Set(arr.map((s) => s.trim()).filter(Boolean)));
}
