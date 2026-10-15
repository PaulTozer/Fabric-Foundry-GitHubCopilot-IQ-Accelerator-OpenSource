'use client';

import type { Conversation } from '@/lib/types';

interface SidebarProps {
  // ── Chat history ──
  conversations: Conversation[];
  activeId:      string | null;
  onSelect:      (id: string) => void;
  onNew:         () => void;

  onOpenAgentManager: () => void;
}

export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onOpenAgentManager,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-brand-line bg-white">
      <h2 className="border-b border-brand-line px-3 py-3 text-xs font-semibold uppercase tracking-wide">
        Chats <span className="text-brand-grey/40">({conversations.length})</span>
      </h2>

      {/* Wrapper gives chat history a constrained height so its
          inner `overflow-y-auto` scroll region actually engages.
          `min-h-0` is required because the default `min-height: auto`
          on flex items lets content blow past the parent. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ChatsPanel
          conversations={conversations}
          activeId={activeId}
          onSelect={onSelect}
          onNew={onNew}
        />
      </div>

      <div className="border-t border-brand-line px-3 py-2">
        <button type="button" onClick={onOpenAgentManager} className="min-h-9 w-full rounded-lg border border-brand-line px-3 py-2 text-left text-sm font-semibold">Agent Manager</button>
      </div>
      <div className="border-t border-brand-line px-3 py-2 text-[11px] leading-tight text-brand-grey/50">
        Powered by <span className="font-medium text-brand-grey/80">Copilot SDK BYOK</span><br />
        Connect approved real MCP sources in Agent Manager
      </div>
    </aside>
  );
}

// ─── Chats panel (preserved original layout) ───────────────────────────────

function ChatsPanel({
  conversations,
  activeId,
  onSelect,
  onNew,
}: {
  conversations: Conversation[];
  activeId:      string | null;
  onSelect:      (id: string) => void;
  onNew:         () => void;
}) {
  return (
    <>
      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full rounded-full bg-brand-purple px-4 py-2.5 text-center text-xs font-bold uppercase tracking-wide text-white shadow-xs transition hover:bg-brand-green-dark"
        >
          + New chat
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-4">
        {conversations.length === 0 ? (
          <p className="px-2 py-1 text-xs text-brand-grey/50">No conversations yet</p>
        ) : (
          conversations.map((c) => (
            <button
              key={c.id}
              onClick={() => onSelect(c.id)}
              className={
                'mb-1 block w-full truncate rounded-md px-3 py-2 text-left text-sm transition ' +
                (c.id === activeId
                  ? 'bg-brand-paper font-medium text-brand-grey'
                  : 'text-brand-grey/80 hover:bg-brand-paper')
              }
              title={c.title}
            >
              {c.title || 'Untitled'}
            </button>
          ))
        )}
      </nav>
    </>
  );
}
