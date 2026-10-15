// Subscription-scoped entry point for `azd up`. Creates the resource group
// and delegates resource creation to ./resources.bicep at RG scope.
//
// Copilot SDK BYOK, Foundry Council and optional Work IQ. No provisioning here
// creates Fabric data, model deployments or Work IQ agents. An explicit preup
// hook can prepare dedicated sample Fabric items before this template runs.

targetScope = 'subscription'

// ───────────────────────── Required parameters ─────────────────────────
@minLength(1)
@maxLength(64)
@description('Name of the azd environment (used as a suffix on every resource).')
param environmentName string

@minLength(1)
@description('Azure region for all resources (e.g. uksouth).')
param location string

@description('Foundry project endpoint URL, e.g. https://<foundry-account>.services.ai.azure.com/api/projects/EnterpriseKG.')
param projectEndpoint string = ''

@description('Foundry model deployment name used by M6 strategists.')
param modelDeploymentName string = 'gpt-5.4'

@description('Fabric workspace ID hosting EnterpriseKG_Lakehouse + EnterpriseKG_Ontology.')
param fabricWorkspaceId string = ''

@description('Fabric lakehouse item ID (EnterpriseKG_Lakehouse).')
param fabricLakehouseId string = ''

@description('Fabric ontology item ID (EnterpriseKG_Ontology).')
param fabricOntologyId string = ''

@description('Fabric SQL endpoint hostname (without https://).')
param fabricSqlEndpoint string = ''

param fabricOntologyMcpUrl string = ''

@allowed(['0', '1'])
@description('Opt-in synthetic Fabric sample prepared by the azd preup hook. Defaults off; no Fabric ARM resources.')
param fabricDeployTestOntology string = '0'
param fabricTestWorkspaceId string = ''
param fabricTestLakehouseId string = ''
param fabricTestOntologyId string = ''
param fabricTestSqlEndpoint string = ''
param fabricTestSqlDatabase string = ''
param fabricTestOntologyMcpUrl string = ''
param fabricTestProvisioningStatus string = ''
param fabricTestSchemaVersion string = ''

var sampleSelected = fabricDeployTestOntology == '1'
var selectedFabric = sampleSelected ? {
  workspaceId: fabricTestWorkspaceId
  lakehouseId: fabricTestLakehouseId
  ontologyId: fabricTestOntologyId
  sqlEndpoint: fabricTestSqlEndpoint
  sqlDatabase: fabricTestSqlDatabase
  ontologyMcpUrl: fabricTestOntologyMcpUrl
} : {
  workspaceId: fabricWorkspaceId
  lakehouseId: fabricLakehouseId
  ontologyId: fabricOntologyId
  sqlEndpoint: fabricSqlEndpoint
  sqlDatabase: fabricSqlDatabase
  ontologyMcpUrl: fabricOntologyMcpUrl
}
var configuredFabric = !empty(fabricSqlEndpoint) || !empty(fabricOntologyId) || !empty(fabricOntologyMcpUrl)
var fabricDataMode = sampleSelected ? 'test-sample' : configuredFabric ? 'existing' : 'unconfigured'

@allowed(['0', '1'])
param iqDemoMode string = '0'

@allowed(['0', '1'])
param copilotUseAzureIdentity string = '0'

@allowed(['0.5', '1.0', '2.0'])
@description('MCP CPU allocation; memory is paired at 2 GiB per vCPU.')
param mcpCpu string = '2.0'

@secure()
@minLength(32)
@description('Random server-to-server secret shared by UI and MCP, independent of delegated Fabric tokens.')
param mcpServiceToken string

@description('Fabric SQL database name (default: EnterpriseKG_Lakehouse).')
param fabricSqlDatabase string = 'EnterpriseKG_Lakehouse'

@description('Optional cost-tier model deployments used by M6 Council. Both fall back to modelDeploymentName when blank.')
param modelDeploymentNameFast string = ''
param modelDeploymentNamePro  string = ''

@description('Embedding model deployment used by M6 few-shot retrieval (default: text-embedding-ada-002).')
param embeddingDeploymentName string = 'text-embedding-ada-002'

@description('Foundry Work IQ (M7) agent name. Empty disables M7.')
param m7AgentName string = ''

@allowed(['openai', 'azure', 'anthropic'])
param copilotProvider string = 'openai'
param copilotBaseUrl string = ''
param copilotModel string = ''
@allowed(['responses', 'completions'])
param copilotWireApi string = 'completions'
param copilotAzureApiVersion string = ''
@description('Approved Cognitive Services account ARM resource ID for enabled deployment discovery.')
param copilotAzureResourceId string = ''
@description('Nonsecret exact-origin/auth-reference/tool trust policy JSON for user remote MCP connections.')
param iqMcpTrustPolicy string = ''
@description('Server-owned authentication reference metadata JSON, never credential values.')
param iqMcpAuthReferences string = ''
@secure()
param copilotApiKey string = ''
@secure()
param copilotBearerToken string = ''

@minLength(36)
@maxLength(36)
@description('Entra ID tenant restricted to sign in to the chat UI (typically your home tenant ID).')
param entraTenantId string

@minLength(36)
@maxLength(36)
@description('Application (client) ID of the "EnterpriseIQ" Entra app registration. See infra/README.md to create it.')
param entraClientId string

@secure()
@description('Client secret for the "EnterpriseIQ" Entra app registration. Stored in Container Apps as a secret reference.')
param entraClientSecret string

@minLength(1)
@maxLength(20)
@description('Active UI Easy Auth signing secret name. Required on every provision; never fall back to generated authority.')
param webuiAuthSigningSecretName string

@minLength(1)
@maxLength(20)
@description('Active UI Easy Auth encryption secret name, distinct from the signing secret.')
param webuiAuthEncryptionSecretName string

@secure()
@minLength(44)
@maxLength(44)
@description('Current 32-byte signing key in canonical Base64, supplied through protected process inputs. Reuse the exact active value on reprovision.')
param webuiAuthSigningKey string

@secure()
@minLength(44)
@maxLength(44)
@description('Current 32-byte encryption key in canonical Base64, supplied through protected process inputs. Reuse the exact active value on reprovision.')
param webuiAuthEncryptionKey string

// ───────────────────────── Image preservation ─────────────────────────
// On a fresh `azd up`, these are empty and the bicep falls back to the
// k8se/quickstart placeholder. `azd deploy <service>` then pushes the
// real image, writes SERVICE_<NAME>_IMAGE_NAME into the azd .env, and
// subsequent `azd provision` runs feed it back here so we DON'T reset
// the Container App to the placeholder. Without this, any
// `azd provision --no-state` (or schema change that touches the
// container resource) wipes out the deployed image and the apps go
// dark until the next `azd deploy`.
@description('Last-deployed mcp image. Empty on first deploy; azd injects it from SERVICE_MCP_IMAGE_NAME after each push.')
param mcpImageName string = ''

@description('Last-deployed webui image. Empty on first deploy; azd injects it from SERVICE_WEBUI_IMAGE_NAME after each push.')
param webuiImageName string = ''

// ───────────────────────── Naming ─────────────────────────
var rgName = 'rg-${environmentName}'
var tags = {
  'azd-env-name': environmentName
  product:        'EnterpriseIQ'
  purpose:        'user-testing'
}

// ───────────────────────── RG + resources ─────────────────────────
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name:     rgName
  location: location
  tags:     tags
}

module resources './resources.bicep' = {
  name:  'resources'
  scope: rg
  params: {
    location:             location
    environmentName:      environmentName
    tags:                 tags
    projectEndpoint:      projectEndpoint
    modelDeploymentName:  modelDeploymentName
    fabricWorkspaceId:    selectedFabric.workspaceId
    fabricLakehouseId:    selectedFabric.lakehouseId
    fabricOntologyId:     selectedFabric.ontologyId
    fabricSqlEndpoint:    selectedFabric.sqlEndpoint
    fabricOntologyMcpUrl: selectedFabric.ontologyMcpUrl
    fabricDataMode:       fabricDataMode
    iqDemoMode: iqDemoMode
    copilotUseAzureIdentity: copilotUseAzureIdentity
    mcpCpu: mcpCpu
    mcpServiceToken: mcpServiceToken
    fabricSqlDatabase:    selectedFabric.sqlDatabase
    modelDeploymentNameFast: modelDeploymentNameFast
    modelDeploymentNamePro:  modelDeploymentNamePro
    embeddingDeploymentName: embeddingDeploymentName
    m7AgentName:          m7AgentName
    copilotProvider: copilotProvider
    copilotBaseUrl: copilotBaseUrl
    copilotModel: copilotModel
    copilotWireApi: copilotWireApi
    copilotAzureApiVersion: copilotAzureApiVersion
    copilotAzureResourceId: copilotAzureResourceId
    iqMcpTrustPolicy: iqMcpTrustPolicy
    iqMcpAuthReferences: iqMcpAuthReferences
    copilotApiKey: copilotApiKey
    copilotBearerToken: copilotBearerToken
    entraTenantId:        entraTenantId
    entraClientId:        entraClientId
    entraClientSecret:    entraClientSecret
    webuiAuthSigningSecretName: webuiAuthSigningSecretName
    webuiAuthEncryptionSecretName: webuiAuthEncryptionSecretName
    webuiAuthSigningKey: webuiAuthSigningKey
    webuiAuthEncryptionKey: webuiAuthEncryptionKey
    mcpImageName:         mcpImageName
    webuiImageName:       webuiImageName
  }
}

// ───────────────────────── Outputs (azd reads these) ─────────────────────────
output AZURE_RESOURCE_GROUP              string = rg.name
output AZURE_LOCATION                    string = location
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.containerRegistryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME     string = resources.outputs.containerRegistryName
output AZURE_CONTAINER_APPS_ENV_ID       string = resources.outputs.containerAppsEnvironmentId
output WEBUI_FQDN                        string = resources.outputs.webuiFqdn
output MCP_INTERNAL_URL                  string = resources.outputs.mcpInternalUrl
output MCP_MANAGED_IDENTITY_PRINCIPAL_ID  string = resources.outputs.managedIdentityPrincipalId
output FABRIC_ACTIVE_WORKSPACE_ID string = selectedFabric.workspaceId
output FABRIC_ACTIVE_LAKEHOUSE_ID string = selectedFabric.lakehouseId
output FABRIC_ACTIVE_ONTOLOGY_ID string = selectedFabric.ontologyId
output FABRIC_ACTIVE_SQL_ENDPOINT string = selectedFabric.sqlEndpoint
output FABRIC_ACTIVE_SQL_DATABASE string = selectedFabric.sqlDatabase
output FABRIC_ACTIVE_ONTOLOGY_MCP_URL string = selectedFabric.ontologyMcpUrl
output FABRIC_ACTIVE_DATA_MODE string = fabricDataMode
output FABRIC_ACTIVE_SAMPLE_STATUS string = sampleSelected ? fabricTestProvisioningStatus : ''
output FABRIC_ACTIVE_SAMPLE_VERSION string = sampleSelected ? fabricTestSchemaVersion : ''
