'use client';

import Image from 'next/image';
import { SettingsMenu } from '@/components/SettingsMenu';
import type { SelectableMethodId } from '@/lib/types';
import type { OperatorConfiguration } from '@/lib/agent-config';
import type { FabricWorkspaceSettingsProps } from './FabricWorkspaceSettings';

interface HeaderProps {
  /** Currently selected agent methodology. */
  method:         SelectableMethodId;
  /** Update the methodology selection. */
  onMethodChange: (m: SelectableMethodId) => void;
  /** True while a request is in-flight; locks the picker. */
  pending?:       boolean;
  /** Whether the `query_ontology` tool is exposed to M4/M6. */
  ontologyEnabled:         boolean;
  /** Toggle ontology on/off (no-op for non-M4/M6 methods). */
  onOntologyEnabledChange: (v: boolean) => void;
  /** Explicit opt-in for configured Work IQ MCP tools on SDK turns. */
  workIQEnabled:           boolean;
  onWorkIQEnabledChange:   (v: boolean) => void;
  selectedModel: string | null;
  configuration: OperatorConfiguration | null;
  onModelChange: (id: string) => void;
  onOpenAgentManager: () => void;
  fabricWorkspace: FabricWorkspaceSettingsProps;
}

/**
 * Top header. Example Enterprise wordmark left; settings cog right.
 */
export function Header({
  method,
  onMethodChange,
  pending,
  ontologyEnabled,
  onOntologyEnabledChange,
  workIQEnabled,
  onWorkIQEnabledChange,
  selectedModel, configuration, onModelChange, onOpenAgentManager, fabricWorkspace,
}: HeaderProps) {
  return (
    <header className="flex items-center justify-between border-b border-black/40 bg-brand-grey px-6 py-3 text-white">
      <div className="flex items-center gap-3">
        <Image
          src="/branding/accelerator.svg"
          alt="IQ accelerator"
          width={36}
          height={36}
          priority
          className="h-8 w-auto"
        />
        <span className="hidden h-6 w-px bg-white/25 sm:block" />
        <div className="hidden flex-col leading-tight sm:flex">
          <span className="font-display text-lg font-bold tracking-tight text-white">
            Fabric Foundry GitHubCopilot <span className="text-brand-purple">IQ</span>
          </span>
          <span className="text-[11px] uppercase tracking-wide text-white/50">
            Knowledge graph intelligence
          </span>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <SettingsMenu
          method={method}
          onMethodChange={onMethodChange}
          ontologyEnabled={ontologyEnabled}
          onOntologyEnabledChange={onOntologyEnabledChange}
          workIQEnabled={workIQEnabled}
          onWorkIQEnabledChange={onWorkIQEnabledChange}
          pending={pending}
          selectedModel={selectedModel} configuration={configuration}
          onModelChange={onModelChange} onOpenAgentManager={onOpenAgentManager}
          fabricWorkspace={fabricWorkspace}
        />
      </div>
    </header>
  );
}
