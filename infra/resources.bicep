// All resources except the Resource Group itself, deployed at RG scope.
//
// Architecture:
//   - Separate managed identities for image pulls; only the MCP identity
//     receives approved model/Fabric invocation permissions.
//   - Log Analytics workspace + Container Apps environment (consumption).
//   - ACR (Basic).
//   - mcp container app   : ingress = internal (only callable inside the env).
//   - webui container app : ingress = external + Easy Auth (Entra ID, tenant
//                           restricted to entraTenantId).
//
// All non-secret config is passed as plain env vars; the Entra client secret
// is stored as a Container Apps `secret` and surfaced via secretRef.

targetScope = 'resourceGroup'

// ───────────────────────── Parameters ─────────────────────────
param location string
param environmentName string
param tags object

param projectEndpoint string
param modelDeploymentName string
param fabricWorkspaceId string
param fabricLakehouseId string
param fabricOntologyId string
param fabricSqlEndpoint string
param fabricOntologyMcpUrl string = ''
param fabricDataMode string = 'unconfigured'
param iqDemoMode string = '0'
param copilotUseAzureIdentity string = '0'
param mcpCpu string = '2.0'
@secure()
param mcpServiceToken string
param fabricSqlDatabase string
param modelDeploymentNameFast string = ''
param modelDeploymentNamePro string  = ''
param embeddingDeploymentName string = 'text-embedding-ada-002'

@description('Foundry Work IQ (M7) agent name. Empty disables M7.')
param m7AgentName string = ''
param copilotProvider string
param copilotBaseUrl string
param copilotModel string
param copilotWireApi string
param copilotAzureApiVersion string
param copilotAzureResourceId string = ''
param iqMcpTrustPolicy string = ''
param iqMcpAuthReferences string = ''
@secure()
param copilotApiKey string
@secure()
param copilotBearerToken string

param entraTenantId string
param entraClientId string
@secure()
param entraClientSecret string

@minLength(1)
@maxLength(20)
param webuiAuthSigningSecretName string
@minLength(1)
@maxLength(20)
param webuiAuthEncryptionSecretName string
@secure()
@minLength(44)
@maxLength(44)
param webuiAuthSigningKey string
@secure()
@minLength(44)
@maxLength(44)
param webuiAuthEncryptionKey string

// Empty on first deploy; populated from azd's SERVICE_<NAME>_IMAGE_NAME
// after each successful `azd deploy`. See main.bicep for the rationale.
param mcpImageName string = ''
param webuiImageName string = ''

var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var resolvedMcpImage   = empty(mcpImageName)   ? placeholderImage : mcpImageName
var resolvedWebuiImage = empty(webuiImageName) ? placeholderImage : webuiImageName

// ───────────────────────── Naming helpers ─────────────────────────
var token        = uniqueString(subscription().id, environmentName, location)
var acrName      = toLower('acr${take(replace(environmentName, '-', ''), 8)}${take(token, 6)}')
var lawName      = 'log-${environmentName}'
var envName      = 'cae-${environmentName}'
var uamiName     = 'id-${environmentName}'
var mcpAppName   = 'ca-${environmentName}-mcp'
var webuiAppName = 'ca-${environmentName}-webui'
var mcpMemory = {
  '0.5': '1Gi'
  '1.0': '2Gi'
  '2.0': '4Gi'
}

// ───────────────────────── User-assigned managed identity ─────────────────────────
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name:     uamiName
  location: location
  tags:     tags
}

resource webuiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${environmentName}-webui'
  location: location
  tags: tags
}

// ───────────────────────── Log Analytics ─────────────────────────
resource law 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name:     lawName
  location: location
  tags:     tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ───────────────────────── Azure Container Registry ─────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name:     acrName
  location: location
  tags: union(tags, {
    'azd-service-name': 'shared-acr'
  })
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

// Image pulls use separate identities without registry admin credentials.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name:  guid(acr.id, uami.id, acrPullRoleId)
  scope: acr
  properties: {
    principalId:      uami.properties.principalId
    principalType:    'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

resource webuiAcrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, webuiIdentity.id, acrPullRoleId)
  scope: acr
  properties: {
    principalId: webuiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

// ───────────────────────── Container Apps environment ─────────────────────────
resource cae 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name:     envName
  location: location
  tags:     tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey:  law.listKeys().primarySharedKey
      }
    }
  }
}

// ───────────────────────── MCP container app (internal ingress) ─────────────────────────
resource mcpApp 'Microsoft.App/containerApps@2025-01-01' = {
  name:     mcpAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'mcp'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: concat(
        [{ name: 'mcp-service-token', value: mcpServiceToken }],
        empty(copilotApiKey) ? [] : [{ name: 'copilot-api-key', value: copilotApiKey }],
        empty(copilotBearerToken) ? [] : [{ name: 'copilot-bearer-token', value: copilotBearerToken }]
      )
      ingress: {
        // Internal-only: reachable from webui at https://<app>.internal.<env-domain>
        external:      false
        targetPort:    8765
        transport:     'auto'
        allowInsecure: false
      }
      registries: [
        {
          server:   acr.properties.loginServer
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name:  'mcp'
          // Resolved from `mcpImageName` (empty → placeholder). azd writes
          // SERVICE_MCP_IMAGE_NAME into .env after each deploy and feeds
          // it back through main.parameters.json so subsequent provisions
          // don't reset us to the quickstart image.
          image: resolvedMcpImage
          resources: {
            cpu:    json(mcpCpu)
            memory: mcpMemory[mcpCpu]
          }
          probes: [
            { type: 'Startup', httpGet: { path: '/healthz', port: 8765 }, periodSeconds: 10, failureThreshold: 30 }
            { type: 'Readiness', httpGet: { path: '/healthz', port: 8765 }, periodSeconds: 10, failureThreshold: 3 }
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8765 }, periodSeconds: 30, failureThreshold: 3 }
          ]
          env: [
            { name: 'MCP_SERVICE_TOKEN', secretRef: 'mcp-service-token' }
            { name: 'IQ_DEMO_MODE', value: iqDemoMode }
            { name: 'IQ_ENVIRONMENT', value: 'production' }
            { name: 'IQ_MCP_TRUST_POLICY', value: empty(iqMcpTrustPolicy) ? '{"origins":[]}' : iqMcpTrustPolicy }
            { name: 'IQ_MCP_AUTH_REFERENCES', value: empty(iqMcpAuthReferences) ? '[]' : iqMcpAuthReferences }
            { name: 'COPILOT_USE_AZURE_IDENTITY', value: copilotUseAzureIdentity }
            { name: 'COPILOT_PROVIDER', value: copilotProvider }
            { name: 'COPILOT_BASE_URL', value: copilotBaseUrl }
            { name: 'COPILOT_MODEL', value: copilotModel }
            { name: 'COPILOT_WIRE_API', value: copilotWireApi }
            { name: 'COPILOT_AZURE_API_VERSION', value: copilotAzureApiVersion }
            { name: 'COPILOT_AZURE_RESOURCE_ID', value: copilotAzureResourceId }
            union({ name: 'COPILOT_API_KEY' }, empty(copilotApiKey) ? { value: '' } : { secretRef: 'copilot-api-key' })
            union({ name: 'COPILOT_BEARER_TOKEN' }, empty(copilotBearerToken) ? { value: '' } : { secretRef: 'copilot-bearer-token' })
            { name: 'PROJECT_ENDPOINT',         value: projectEndpoint }
            { name: 'MODEL_DEPLOYMENT_NAME',    value: modelDeploymentName }
            { name: 'MODEL_DEPLOYMENT_NAME_FAST', value: modelDeploymentNameFast }
            { name: 'MODEL_DEPLOYMENT_NAME_PRO',  value: modelDeploymentNamePro }
            { name: 'EMBEDDING_DEPLOYMENT_NAME', value: embeddingDeploymentName }
            { name: 'FABRIC_WORKSPACE_ID',      value: fabricWorkspaceId }
            { name: 'FABRIC_LAKEHOUSE_ID',      value: fabricLakehouseId }
            { name: 'FABRIC_ONTOLOGY_ID',       value: fabricOntologyId }
            { name: 'FABRIC_SQL_ENDPOINT',      value: fabricSqlEndpoint }
            { name: 'FABRIC_ONTOLOGY_MCP_URL', value: fabricOntologyMcpUrl }
            { name: 'FABRIC_SQL_DATABASE',      value: fabricSqlDatabase }
            { name: 'FABRIC_DATA_MODE', value: fabricDataMode }
            { name: 'M7_AGENT_NAME',            value: m7AgentName }
            { name: 'AZURE_CLIENT_ID',          value: uami.properties.clientId }
            { name: 'ENTERPRISEKG_HOST',             value: '0.0.0.0' }
            { name: 'ENTERPRISEKG_PORT',             value: '8765' }
            // Internal-only ingress, so we trust the network boundary and
            // disable FastMCP's DNS-rebinding protection (which otherwise
            // 421s any Host header that isn't localhost).
            { name: 'ENTERPRISEKG_DISABLE_DNS_REBINDING_PROTECTION', value: '1' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ───────────────────────── WebUI container app (external + Easy Auth) ─────────────────────────
resource webuiApp 'Microsoft.App/containerApps@2025-01-01' = {
  name:     webuiAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'webui'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${webuiIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external:      true
        targetPort:    3000
        transport:     'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'mcp-service-token'
          value: mcpServiceToken
        }
        {
          name:  'entra-client-secret'
          value: entraClientSecret
        }
        {
          name: webuiAuthSigningSecretName
          value: webuiAuthSigningKey
        }
        {
          name: webuiAuthEncryptionSecretName
          value: webuiAuthEncryptionKey
        }
      ]
      registries: [
        {
          server:   acr.properties.loginServer
          identity: webuiIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name:  'webui'
          image: resolvedWebuiImage
          resources: {
            cpu:    json('0.5')
            memory: '1Gi'
          }
          probes: [
            { type: 'Startup', tcpSocket: { port: 3000 }, periodSeconds: 10, failureThreshold: 30 }
            { type: 'Readiness', tcpSocket: { port: 3000 }, periodSeconds: 10, failureThreshold: 3 }
            { type: 'Liveness', tcpSocket: { port: 3000 }, periodSeconds: 30, failureThreshold: 3 }
          ]
          env: [
            { name: 'MCP_SERVICE_TOKEN', secretRef: 'mcp-service-token' }
            { name: 'IQ_DEMO_MODE', value: iqDemoMode }
            // Internal MCP URL — webui calls this from /api/chat over the
            // Container Apps environment's private DNS.
            { name: 'MCP_BASE_URL', value: 'https://${mcpApp.properties.configuration.ingress.fqdn}' }
            { name: 'AZURE_CLIENT_ID', value: webuiIdentity.properties.clientId }
            // Surfaced for client-side display.
            { name: 'NEXT_PUBLIC_APP_NAME', value: 'Fabric Foundry GitHubCopilot IQ Accelerator' }
            { name: 'NEXT_PUBLIC_BRAND_RED', value: '#00A859' }
            // Runtime AAD config exposed via /api/me so the browser can
            // bootstrap MSAL.js without baking values into the static
            // bundle at `next build` time. Avoids the fragility of
            // propagating --build-arg through ACR Tasks remote builds.
            { name: 'AAD_CLIENT_ID', value: entraClientId }
            { name: 'AAD_TENANT_ID', value: entraTenantId }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        // Process-local uploads require one UI replica.
        maxReplicas: 1
      }
    }
  }
}

// Easy Auth — Entra ID, single-tenant (locked to the operator's tenant).
// Configured as a child authConfig resource so the secret reference and
// tenant lock are explicit.
resource webuiAuth 'Microsoft.App/containerApps/authConfigs@2025-01-01' = {
  parent: webuiApp
  name:   'current'
  properties: {
    encryptionSettings: {
      containerAppAuthSigningSecretName: webuiAuthSigningSecretName
      containerAppAuthEncryptionSecretName: webuiAuthEncryptionSecretName
    }
    platform: {
      enabled:        true
      runtimeVersion: '~1'
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider:          'azureActiveDirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId:                entraClientId
          clientSecretSettingName: 'entra-client-secret'
          openIdIssuer:            '${environment().authentication.loginEndpoint}${entraTenantId}/v2.0'
        }
        validation: {
          // Entra v2.0 issuer: the id_token's `aud` claim is the client-ID
          // GUID, NOT `api://{clientId}`. Setting only the URI form here will
          // cause Easy Auth to 401 every request after a successful login.
          allowedAudiences: [
            entraClientId
            'api://${entraClientId}'
          ]
          defaultAuthorizationPolicy: {
            allowedApplications: [entraClientId]
          }
        }
      }
    }
    login: {
      tokenStore: {
        enabled: false
      }
    }
  }
}

// ───────────────────────── Outputs ─────────────────────────
output containerRegistryLoginServer string = acr.properties.loginServer
output containerRegistryName        string = acr.name
output containerAppsEnvironmentId   string = cae.id
output webuiFqdn                    string = webuiApp.properties.configuration.ingress.fqdn
output mcpInternalUrl               string = 'https://${mcpApp.properties.configuration.ingress.fqdn}'
output managedIdentityClientId      string = uami.properties.clientId
output managedIdentityPrincipalId   string = uami.properties.principalId
