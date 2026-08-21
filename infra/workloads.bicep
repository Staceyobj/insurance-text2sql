// Azure deployment, phase 2 of 2: the container app (API) and the Manual
// seed Job. Deployed AFTER the image has been pushed to ACR — revision
// provisioning validates the image pull at PUT time (see base.bicep header).
//
// All inputs come from base.bicep outputs; `make infra-up` wires them.

@description('Name prefix; must match the base deployment.')
param baseName string = 't2s'

@description('Managed environment resource id (base output environmentId).')
param environmentId string

@description('User-assigned identity resource id (base output identityId).')
param identityId string

@description('ACR login server (base output acrLoginServer).')
param acrLoginServer string

@description('Key Vault secret URIs (base outputs secretUri*). Passed through, never constructed: the DNS suffix encodes the cloud, and Container Apps rejects unrecognized suffixes.')
param secretUriZhipu string

param secretUriDatabase string

param secretUriAdmin string

@description('Image tag pushed to ACR.')
param imageTag string = 'dev'

var appName = '${baseName}-app'
var jobName = '${baseName}-seed'
var acrImage = '${acrLoginServer}/insurance-text2sql:${imageTag}'

// Referenced identities (ACR pull, Key Vault secret refs) must be attached
// here, or the first revision fails with "managed identity not found".
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
      secrets: [
        {
          name: 'zhipuai-api-key'
          keyVaultUrl: secretUriZhipu
          identity: identityId
        }
        {
          name: 'database-url'
          keyVaultUrl: secretUriDatabase
          identity: identityId
        }
      ]
      ingress: {
        external: false // internal-only (DEPLOYMENT.md §1): no public exposure
        targetPort: 8000
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: acrImage
          env: [
            {
              name: 'ZHIPUAI_API_KEY'
              secretRef: 'zhipuai-api-key'
            }
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'LLM_BASE_URL'
              value: 'https://open.bigmodel.cn/api/paas/v4'
            }
            {
              name: 'LLM_MODEL'
              value: 'glm-4.7'
            }
            {
              name: 'LLM_THINKING_ENABLED'
              value: 'false'
            }
            {
              name: 'ROW_LIMIT'
              value: '200'
            }
            {
              name: 'MAX_RETRIES'
              value: '2'
            }
            {
              name: 'LOG_LEVEL'
              value: 'INFO'
            }
          ]
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0 // cold start accepted (DEPLOYMENT.md §1)
        maxReplicas: 3 // Zhipu rate-limit ceiling
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

// One-shot, destructive (DROP SCHEMA rebuild): Manual trigger only, so
// nothing but a human can start it (DEPLOYMENT.md §5).
resource seedJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      replicaTimeout: 900
      replicaRetryLimit: 0
      triggerType: 'Manual'
      manualTriggerConfig: {
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
      secrets: [
        {
          name: 'admin-database-url'
          keyVaultUrl: secretUriAdmin
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'seed'
          image: acrImage
          command: [
            'python'
          ]
          args: [
            'db/seed.py'
          ]
          env: [
            {
              name: 'ADMIN_DATABASE_URL'
              secretRef: 'admin-database-url'
            }
          ]
        }
      ]
    }
  }
}

// Internal-only ingress still has a real in-VNet FQDN; used by the M3 smoke.
output appIngressFqdn string = app.properties.configuration.ingress.fqdn
