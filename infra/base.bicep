// Azure deployment, phase 1 of 2 (DEPLOYMENT.md §1–§5): network, database,
// registry, vault, identity, Container Apps environment — everything except
// the workloads. The app and seed Job live in workloads.bicep and deploy only
// AFTER the image exists in ACR: revision provisioning validates the image
// pull at PUT time, so a single-phase deploy fails on a fresh registry.
//
// Deploy (see Makefile `infra-up`):
//   az group create -l eastasia -n rg-t2s-eastasia   // region set once, here
//   az deployment group create -g rg-t2s-eastasia -f infra/base.bicep \
//     -p pgAdminPassword=... zhipuaiApiKey=...
//   ... docker build/push ... then infra/workloads.bicep
//
// Security invariants (DEPLOYMENT.md §2):
//   - the app container receives only DATABASE_URL (t2s_readonly) + LLM config;
//     ADMIN_DATABASE_URL exists only on the Manual-trigger seed Job;
//   - the database has no public endpoint (private access + private DNS zone);
//   - ingress is internal-only; smoke tests run from inside the VNet.

@description('Name prefix; resource names are <baseName>-<kind>-<unique suffix>.')
param baseName string = 't2s'

@secure()
@description('Flexible Server admin login password. Never stored in files.')
param pgAdminPassword string

@secure()
@description('Zhipu API key; written into Key Vault as secret zhipuai-api-key.')
param zhipuaiApiKey string

var suffix = uniqueString(resourceGroup().id)
var rgLocation = resourceGroup().location

var acrName = '${baseName}acr${suffix}'
var kvName = '${baseName}kv${suffix}'
var pgServerName = '${baseName}pg${suffix}'
var identityName = '${baseName}-identity'
var envName = '${baseName}-env'
var vnetName = '${baseName}-vnet'
var dnsZoneName = '${pgServerName}.private.postgres.database.azure.com'

// Both connection strings force TLS (DEPLOYMENT.md §2 note).
var databaseUrl = 'postgresql://t2s_readonly:t2s_readonly@${pgServerName}.private.postgres.database.azure.com/insurance?sslmode=require'
var adminDatabaseUrl = 'postgresql://${baseName}admin:${pgAdminPassword}@${pgServerName}.private.postgres.database.azure.com/insurance?sslmode=require'

// ---------------------------------------------------------------------------
// Network: one VNet, one subnet delegated to Container Apps, one to Postgres.
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: rgLocation
  properties: {
    addressSpace: {
      addressPrefixes: ['10.10.0.0/16']
    }
    subnets: [
      {
        name: 'apps'
        properties: {
          addressPrefix: '10.10.0.0/23' // /23 minimum for a workload-profiles environment
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.10.2.0/24'
          delegations: [
            {
              name: 'Microsoft.DBforPostgreSQL.flexibleServers'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
    ]
  }
}

resource appsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'apps'
}

resource pgSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'postgres'
}

// Private DNS so the *.private.postgres.database.azure.com name resolves
// inside the VNet — required plumbing for private-access Flexible Server.
resource pgDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: dnsZoneName
  location: 'global'
  properties: {}
}

resource pgDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: pgDnsZone
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

// ---------------------------------------------------------------------------
// Database: PostgreSQL 16 Flexible Server, private access (no public endpoint).
// ---------------------------------------------------------------------------

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: pgServerName
  location: rgLocation
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: '${baseName}admin'
    administratorLoginPassword: pgAdminPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: pgSubnet.id
      privateDnsZoneArmResourceId: pgDnsZone.id
    }
  }
}

// The user database is NOT created implicitly on the Bicep path (unlike
// `az postgres flexible-server create --database-name`); without this the
// seed Job dies on connect: database "insurance" does not exist.
resource pgDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: 'insurance'
  properties: {}
}

// ---------------------------------------------------------------------------
// Registry, Key Vault, identity.
// ---------------------------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: rgLocation
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false // pull via managed identity only
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: rgLocation
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true // no access policies; roles below
    publicNetworkAccess: 'Enabled' // az cli management only; secrets still require auth
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: rgLocation
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, identity.id, 'Key Vault Secrets User')
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull (GUID verified via `az role definition list`)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault content is provisioned by the template itself (DEPLOYMENT.md §4):
// no manual `az keyvault secret set` runbook step.
resource kvZhipuKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'zhipuai-api-key'
  properties: {
    value: zhipuaiApiKey
  }
}

resource kvDatabaseUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'database-url'
  properties: {
    value: databaseUrl
  }
}

// Deleted after first seed if you want the strictest posture (M3 runbook);
// note it is an ARM resource, so the next `infra-up` re-creates it — with
// the freshly rotated admin password (see DEPLOYMENT.md §4).
resource kvAdminDatabaseUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'admin-database-url'
  properties: {
    value: adminDatabaseUrl
  }
}

// ---------------------------------------------------------------------------
// Container Apps environment: workload profiles, Consumption only — never a
// dedicated profile (~$145/mo baseline).
// ---------------------------------------------------------------------------

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: rgLocation
  properties: {
    vnetConfiguration: {
      internal: true // the environment's own ingress IPs stay inside the VNet
      infrastructureSubnetId: appsSubnet.id
    }
    zoneRedundant: false
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

output acrLoginServer string = acr.properties.loginServer
output postgresHost string = '${pgServerName}.private.postgres.database.azure.com'
output keyVaultName string = kvName
output identityId string = identity.id
output environmentId string = env.id
// Pass secret URIs through, never construct them in workloads.bicep: the DNS
// suffix encodes the cloud (vault.azure.net in global), and Container Apps
// rejects URIs whose suffix it does not recognize.
output secretUriZhipu string = kvZhipuKey.properties.secretUri
output secretUriDatabase string = kvDatabaseUrl.properties.secretUri
output secretUriAdmin string = kvAdminDatabaseUrl.properties.secretUri
