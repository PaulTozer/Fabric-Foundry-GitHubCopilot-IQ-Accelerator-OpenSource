export interface FabricSelection {
  workspaceId: string;
  lakehouseId: string | null;
  ontologyId: string | null;
}
export interface FabricInventory {
  workspaceId: string;
  lakehouses: { id: string; name: string }[];
  ontologies: { id: string; name: string }[];
}
export interface FabricStatus {
  selection: FabricSelection;
  sql: boolean;
  ontology: boolean;
  fabricDataMode: string;
  metadataValidated: boolean;
}
export function fabricId(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error('Fabric IDs must be hyphenated UUIDs.');
  }
  return value.toLowerCase();
}
export function validateFabricSelection(value: unknown): FabricSelection {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid Fabric selection.');
  const data = value as Record<string, unknown>;
  if (Object.keys(data).some(key => !['workspaceId', 'lakehouseId', 'ontologyId'].includes(key))) {
    throw new Error('Only Fabric workspace and item IDs are accepted.');
  }
  const result = { workspaceId: fabricId(data.workspaceId),
    lakehouseId: data.lakehouseId === null ? null : fabricId(data.lakehouseId),
    ontologyId: data.ontologyId === null ? null : fabricId(data.ontologyId) };
  if (!result.lakehouseId && !result.ontologyId) throw new Error('Select at least one Fabric item.');
  return result;
}
