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

## 2c. Secrets — no Key Vault

Secrets are **GitHub repository secrets**, injected as environment variables.
There is no vault.

A Key Vault (`techtonic-kv`) was created and then removed. Granting access to it
needs `Microsoft.Authorization/roleAssignments/write` — Owner or User Access
Administrator — and this project holds only Contributor. A Contributor can
create a vault but cannot grant access to it, cannot switch it to the
access-policy model (changing the permission model needs the same right), and
the tenant forces RBAC on new vaults regardless. So the vault would have sat in
the specs unreadable, which reads as configured and is worse than absent.

The specs reference environment variables directly:

| Reference | Where |
|---|---|
| `${AZURE_CLIENT_SECRET}` | `fabric/01-scaffolding.yaml` — service principal |
| `${TEAMS_WEBHOOK_URL}` | `dataops/01-monitoring.yaml` — alerting |
| `${PAGERDUTY_ROUTING_KEY}` | `dataops/01-monitoring.yaml` — alerting |

`framework/deploy/_secrets.py` resolves these, and still supports
`keyvault://vault/secret` for a project that does have the access — it checks
the environment first either way, so CI needs no vault round trip.

Running locally needs no secret at all: the deploy scripts fall back to your
`az login` session.

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
