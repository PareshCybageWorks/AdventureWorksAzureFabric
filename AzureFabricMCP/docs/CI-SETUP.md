# CI setup

What has to exist before the deploy workflows can run. Everything here is done
in Azure and GitHub — none of it can be generated from this repository, and
none of it should be committed to it.

Until step 5 is done, **deploy jobs skip rather than fail**. `ci-validate`
needs no credentials and runs regardless.

Once enabled, promotion is a merge:

```
feature/* --PR--> dev --> qa --> uat --> main (tag v* deploys prod)
```

Each branch push deploys to the environment that branch declares in
`fabric/01-scaffolding.yaml`, and validation refuses a spec where those two
disagree.

---

## 1. Service principal — already exists

`AzureFabric_MCP` is the principal to use. The deploy scripts prefer a service
principal and fall back to your `az login` session, so nothing in the framework
needs a CI-specific branch.

| | |
|---|---|
| Display name | `AzureFabric_MCP` |
| Application (client) id | `cd353f61-2c49-4ecb-b9ab-731f5390b8ec` |
| Object id | `275acfcb-d133-4840-90a4-7545136ecb75` |
| Tenant id | `37709cfb-f3b2-4648-aea1-9c828a886147` |

You need a **client secret** for it. If you do not have one to hand, create a
new one — an existing secret cannot be read back:

```bash
az ad app credential reset --id cd353f61-2c49-4ecb-b9ab-731f5390b8ec   --display-name "github-actions" --years 1
```

That prints the secret **once**. Copy it straight into the GitHub secret in
step 3; do not paste it into a file in this repository.

---

## 2. Grant it access

Two things, and the second is the one people miss.

**a. Workspace access — already granted.** Verified on all four:

| Environment | Workspace | Id | Role |
|---|---|---|---|
| dev  | AgenticAIDemo_dev | `13c508c8-8fe8-4539-9cf4-2b4a3b91b32f` | Admin |
| qa   | AgenticAIDemo_qa  | `a07d5fde-5fa5-4978-8c78-295571b43776` | Admin |
| uat  | AgenticAIDemo_uat | `08f3d907-80fa-4e86-adfb-ef7d7e5fc34e` | Admin |
| prod | AgenticAIDemo     | `00e8f132-21d1-41ed-bbfc-ad3a223cf714` | Admin |

Contributor is sufficient for everything the framework does — create items,
deploy, run notebooks. Admin additionally allows deleting the workspace and
changing its access, neither of which any script here needs. Worth narrowing to
Contributor if this principal is ever shared beyond this pipeline.

**b. The tenant setting.** In the Fabric admin portal, enable
**"Service principals can use Fabric APIs"** and include a security group
containing the principal.

Without this the API returns a plausible-looking authorisation error that does
not mention the tenant setting at all, and you will spend the afternoon
checking workspace permissions that were correct the whole time.

---

## 2c. Key Vault — created, needs one role grant

`techtonic-kv` exists and is the vault the specs already reference
(`keyvault://techtonic-kv/fabric-sp-secret` and two alerting secrets).

| | |
|---|---|
| Vault | `techtonic-kv` |
| URI | `https://techtonic-kv.vault.azure.net/` |
| Resource group | `rg-fabric-poc-paresh` (centralindia) |
| Model | RBAC (the tenant forces this on new vaults) |
| Soft delete | 90 days |

**Nobody can read or write its secrets yet, including you.** Granting access
needs `Microsoft.Authorization/roleAssignments/write` — Owner or User Access
Administrator on the subscription or resource group. A Contributor can create
the vault but not grant access to it, and cannot switch it to the access-policy
model either, because changing the permission model requires the same right.

Your account holds **Contributor** on the subscription and nothing more, so
this is not something you can grant yourself. `User Access Administrator` on the
subscription is held by `sagaru@cybage.com` and `vaibhavwa@cybage.com`; several
others hold `Owner`. Either role is sufficient.

Someone with that role needs to run:

```bash
SCOPE="/subscriptions/f7776080-7c77-4452-b9ae-caf61f7d582b/resourceGroups/rg-fabric-poc-paresh/providers/Microsoft.KeyVault/vaults/techtonic-kv"
az role assignment create --assignee-object-id 468e37ae-d46d-48c2-a834-3e32dd4b125a --assignee-principal-type User --role "Key Vault Secrets Officer" --scope "$SCOPE"
az role assignment create --assignee-object-id 275acfcb-d133-4840-90a4-7545136ecb75 --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope "$SCOPE"
```

Officer for you (read and write), Secrets User for `AzureFabric_MCP` (read
only) — it never needs to create a secret.

Then store the secret **yourself**; it should not pass through anyone else's
hands or appear in a transcript:

```bash
az keyvault secret set --vault-name techtonic-kv --name fabric-sp-secret --value "<the client secret>"
```

### Is Key Vault required?

**No.** CI resolves secrets from GitHub repository secrets, and
`framework/deploy/_secrets.py` checks the environment *before* the vault --
deliberately, so a runner needs no second identity and no network round trip.

Key Vault matters for what CI does not cover: running the deploy scripts
locally without exporting a secret into your shell, the Teams and PagerDuty
references in `dataops/01-monitoring.yaml`, and having one place to rotate a
secret rather than several.

If you would rather not use it at all, delete the three `keyvault://` references
and export the variables instead. What is not acceptable is leaving the
references in place unresolvable -- a spec that names a vault nobody can read is
the kind of decoration this framework exists to remove.

---

## 3. Add repository secrets

**Settings → Secrets and variables → Actions → Secrets**

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | `cd353f61-2c49-4ecb-b9ab-731f5390b8ec` |
| `AZURE_CLIENT_SECRET` | the secret from step 1 |
| `AZURE_TENANT_ID` | `37709cfb-f3b2-4648-aea1-9c828a886147` |

Add them at the **repository** level, or per Environment if you want prod to
use a different principal — which is worth doing if prod is a separate tenant
or subscription.

---

## 4. Create the GitHub Environments

**Settings → Environments.** Create `dev`, `qa`, `uat` and `prod` — four,
matching the `environment:` in each workflow job. `uat` is easy to miss; it had
no deploy workflow at all until the branch mapping was declared and checked.

**This is where approval gates actually live.** The spec declares two manual
gates gating prod:

| Gate | Approvers | Confirms |
|---|---|---|
| `uat-signoff` | business-data-owner | UAT figures match expectation |
| `manual-approval` | data-platform-leads | deployment window respected, change reference supplied |

Add those people as **required reviewers on the `prod` Environment**. Nothing
generated from this repository can create them — a job without required
reviewers deploys straight to production however many gates the spec lists.

Consider also setting a deployment branch rule on `prod` so only tags matching
`v*` can deploy to it.

---

## 4b. Capacity

Running on the **FTL64 trial** (`325b3b5d-…`), shared by all four workspaces.
Ample for CI: the full pipeline takes ~13 minutes on it.

The risk is expiry, not speed. When the trial lapses, every environment loses
compute at once — including prod. `fabric/01-scaffolding.yaml` records an F2
fallback that validation deliberately warns about, because F2 is roughly 32x
less compute and Spark does not refuse, it crawls or dies on memory with nothing
in the logs pointing at capacity. Size a replacement against a real
`p_orchestrate_master` run before the trial ends; F8 is the realistic floor.

---

## 5. Turn deploys on

**Settings → Secrets and variables → Actions → Variables**

| Variable | Value |
|---|---|
| `FABRIC_DEPLOY_ENABLED` | `true` |

Every job declaring an `environment` is gated on this. Until it is set, those
jobs skip — so merging the workflows before the rest of this setup exists does
not turn the repository red for a reason that is not a fault.

Set it last, once steps 1–4 are done.

---

## Verify

Run the CI job locally first. A workflow whose first execution is on a pull
request is a workflow whose first failure is public.

```bash
python AzureFabricMCP/framework/generators/validate.py --project 01_demo-project
python AzureFabricMCP/framework/tests/test_monitoring.py
```

Then check the principal can actually reach Fabric, using the same credential
resolution the deploy scripts use:

```bash
AZURE_CLIENT_ID=... AZURE_CLIENT_SECRET=... AZURE_TENANT_ID=... \
  python AzureFabricMCP/framework/deploy/provision_storage.py \
    --project 01_demo-project --env qa --dry-run
```

That reads the workspace and creates nothing. If it lists the four items, the
principal is configured correctly. If it fails on authorisation, revisit step
2b before anything else.

---

## The data-quality gate

`dq-gate` is now enforced. `cd-deploy-qa` runs `tools/run_monitor.py`, which
executes `nb_dq_monitor` in the target workspace, reads the results back, and
fails on any breach at a severity that environment blocks on.

It prints which check, on which table, measured what — because a failed Fabric
notebook reports only "session failed", and a gate nobody can read is a gate
nobody trusts.

`GD-SALES-005` — gold must tie back to silver exactly — previously failed at
4.15%, because 2,945 order lines whose parent order had been quarantined were
dropped by a join in gold. `cascade_quarantine` now removes them at silver
instead, so silver and gold reconcile and the gate passes.

Validation warnings do NOT block. Errors do. Warnings are printed on every run
and reviewed rather than enforced, so a release candidate is never hostage to an
advisory finding.

To see the position without blocking a deploy:

```bash
python AzureFabricMCP/framework/tools/run_monitor.py   --project 01_demo-project --env qa --report-only
```
