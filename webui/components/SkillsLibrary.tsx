'use client';

import { useCallback, useMemo, useState } from 'react';
import type { Skill } from '@/lib/types';
import { filterSkills } from '@/lib/skills-store';

interface SkillsLibraryProps {
  skills:    Skill[];
  /** Drop the skill's prompt into the composer (does NOT send). */
  onUse:     (skill: Skill) => void;
  /** Open editor in "new" mode. */
  onCreate:  () => void;
  /** Open editor for an existing skill. */
  onEdit:    (skill: Skill) => void;
  /** Permanently remove a user-created skill. Built-ins are protected. */
  onDelete:  (id: string) => void;
}

/**
 * Left-sidebar Skills library. Renders:
 *   – a search box
 *   – two collapsible tag-axis filter groups (Business area + Region)
 *     with multi-select chips
 *   – the filtered list of skills as cards with Use / Edit / Delete
 *   – a "+ New skill" CTA at the top
 *
 * All state here is UI-local (filters, expand/collapse). The actual
 * skills array is owned by the page so additions persist via the store.
 */
export function SkillsLibrary({
  skills,
  onUse,
  onCreate,
  onEdit,
  onDelete,
}: SkillsLibraryProps) {
  const [query, setQuery] = useState('');
  const [selectedAreas,   setSelectedAreas]   = useState<string[]>([]);
  const [selectedRegions, setSelectedRegions] = useState<string[]>([]);
  const [showFilters, setShowFilters]         = useState(true);

  // Build the tag-axis option lists from the current library (so any
  // user-added tags also appear). Sorted for stable display.
  const allAreas = useMemo(
    () => Array.from(new Set(skills.flatMap((s) => s.businessAreas))).sort(),
    [skills],
  );
  const allRegions = useMemo(
    () => Array.from(new Set(skills.flatMap((s) => s.regions))).sort(),
    [skills],
  );

  const filtered = useMemo(
    () => filterSkills(skills, query, selectedAreas, selectedRegions),
    [skills, query, selectedAreas, selectedRegions],
  );

  const toggleArea = useCallback((a: string) => {
    setSelectedAreas((prev) =>
      prev.includes(a) ? prev.filter((x) => x !== a) : [...prev, a],
    );
  }, []);
  const toggleRegion = useCallback((r: string) => {
    setSelectedRegions((prev) =>
      prev.includes(r) ? prev.filter((x) => x !== r) : [...prev, r],
    );
  }, []);

  const clearFilters = useCallback(() => {
    setSelectedAreas([]);
    setSelectedRegions([]);
    setQuery('');
  }, []);

  const hasActiveFilter =
    selectedAreas.length > 0 || selectedRegions.length > 0 || query.trim() !== '';

  return (
    <div className="flex h-full flex-col">
      {/* New skill button */}
      <div className="px-3 pt-3">
        <button
          onClick={onCreate}
          className="w-full rounded-lg border border-brand-purple bg-white px-3 py-2 text-left text-sm font-medium text-brand-purple shadow-xs transition hover:bg-brand-purple hover:text-white"
        >
          + New skill
        </button>
      </div>

      {/* Search */}
      <div className="px-3 pt-2">
        <div className="relative">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search skills…"
            className="w-full rounded-md border border-brand-line bg-brand-paper px-3 py-1.5 pr-8 text-xs text-brand-grey outline-hidden transition placeholder:text-brand-grey/40 focus:border-brand-purple focus:ring-2 focus:ring-brand-purple/20"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-brand-grey/40 hover:text-brand-purple"
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* Filter header */}
      <div className="mt-2 flex items-center justify-between px-3">
        <button
          type="button"
          onClick={() => setShowFilters((v) => !v)}
          className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-brand-grey/60 hover:text-brand-grey"
          aria-expanded={showFilters}
        >
          <span>{showFilters ? '▾' : '▸'}</span>
          <span>Filters</span>
          {hasActiveFilter && (
            <span className="ml-1 rounded-full bg-brand-purple px-1.5 py-0.5 text-[9px] font-bold text-white">
              {selectedAreas.length + selectedRegions.length + (query ? 1 : 0)}
            </span>
          )}
        </button>
        {hasActiveFilter && (
          <button
            type="button"
            onClick={clearFilters}
            className="text-[11px] text-brand-grey/50 hover:text-brand-purple"
          >
            clear
          </button>
        )}
      </div>

      {/* Filter chips */}
      {showFilters && (
        <div className="space-y-2 px-3 pt-2">
          <FilterGroup
            label="Business area"
            options={allAreas}
            selected={selectedAreas}
            onToggle={toggleArea}
          />
          <FilterGroup
            label="Region"
            options={allRegions}
            selected={selectedRegions}
            onToggle={toggleRegion}
          />
        </div>
      )}

      {/* Skill list */}
      <div className="mt-3 min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {filtered.length === 0 ? (
          <p className="px-2 py-3 text-xs text-brand-grey/50">
            No skills match the current filters.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {filtered.map((s) => (
              <li key={s.id}>
                <SkillCard
                  skill={s}
                  onUse={() => onUse(s)}
                  onEdit={() => onEdit(s)}
                  onDelete={() => onDelete(s.id)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="border-t border-brand-line px-3 py-2 text-[11px] leading-tight text-brand-grey/50">
        Click <span className="font-medium text-brand-grey/80">Use</span> to drop a skill into the composer.
        Skills persist in your browser only.
      </div>
    </div>
  );
}

// ─── Filter group (chips) ──────────────────────────────────────────────────

interface FilterGroupProps {
  label:    string;
  options:  string[];
  selected: string[];
  onToggle: (value: string) => void;
}

function FilterGroup({ label, options, selected, onToggle }: FilterGroupProps) {
  if (options.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-brand-grey/50">
        {label}
      </div>
      <div className="flex flex-wrap gap-1">
        {options.map((opt) => {
          const on = selected.includes(opt);
          return (
            <button
              key={opt}
              type="button"
              onClick={() => onToggle(opt)}
              aria-pressed={on}
              className={
                'rounded-full border px-2 py-0.5 text-[11px] transition ' +
                (on
                  ? 'border-brand-purple bg-brand-purple text-white shadow-xs'
                  : 'border-brand-line bg-white text-brand-grey/70 hover:border-brand-purple/60 hover:text-brand-purple')
              }
            >
              {opt}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── Single skill card ─────────────────────────────────────────────────────

interface SkillCardProps {
  skill:    Skill;
  onUse:    () => void;
  onEdit:   () => void;
  onDelete: () => void;
}

function SkillCard({ skill, onUse, onEdit, onDelete }: SkillCardProps) {
  const isBuiltin = Boolean(skill.builtin);
  return (
    <div className="group rounded-md border border-brand-line bg-white px-2.5 py-2 shadow-xs transition hover:border-brand-purple/40 hover:shadow-md">
      <button
        type="button"
        onClick={onUse}
        className="w-full text-left"
        title="Drop this skill's prompt into the composer"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="text-sm font-medium leading-snug text-brand-grey">
            {skill.name}
          </div>
          {isBuiltin && (
            <span className="shrink-0 rounded-sm bg-brand-paper px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-brand-grey/60">
              built-in
            </span>
          )}
        </div>
        {skill.description && (
          <p className="mt-0.5 line-clamp-2 text-xs text-brand-grey/60">
            {skill.description}
          </p>
        )}
        <div className="mt-1.5 flex flex-wrap gap-1">
          {skill.regions.map((r) => (
            <TagChip key={'r:' + r} kind="region">{r}</TagChip>
          ))}
          {skill.businessAreas.map((b) => (
            <TagChip key={'b:' + b} kind="area">{b}</TagChip>
          ))}
        </div>
      </button>

      <div className="mt-2 flex items-center justify-end gap-1 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100">
        <button
          type="button"
          onClick={onUse}
          className="rounded-sm bg-brand-purple px-2 py-0.5 text-[11px] font-medium text-white hover:bg-brand-green-dark"
        >
          Use
        </button>
        <button
          type="button"
          onClick={onEdit}
          className="rounded-sm border border-brand-line px-2 py-0.5 text-[11px] text-brand-grey/70 hover:border-brand-purple hover:text-brand-purple"
        >
          {isBuiltin ? 'View' : 'Edit'}
        </button>
        {!isBuiltin && (
          <button
            type="button"
            onClick={() => {
              if (window.confirm(`Delete skill "${skill.name}"?`)) onDelete();
            }}
            className="rounded-sm border border-brand-line px-2 py-0.5 text-[11px] text-brand-grey/70 hover:border-red-400 hover:text-red-600"
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

function TagChip({
  kind,
  children,
}: {
  kind: 'region' | 'area';
  children: React.ReactNode;
}) {
  const cls =
    kind === 'region'
      ? 'border-brand-purple/30 bg-brand-purple/5 text-brand-purple'
      : 'border-brand-line bg-brand-paper text-brand-grey/70';
  return (
    <span className={`rounded-full border ${cls} px-1.5 py-0.5 text-[10px]`}>
      {children}
    </span>
  );
}
