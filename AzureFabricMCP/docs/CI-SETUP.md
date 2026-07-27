# CI setup

What has to exist before the deploy workflows can run. Everything here is done
in Azure and GitHub — none of it can be generated from this repository, and
none of it should be committed to it.

Until step 5 is done, **deploy jobs skip rather than fail**. `ci-validate` and
`ops-monitor` need no credentials and run regardless.

---

## 1. Create a service principal

The deploy scripts prefer a service principal and fall back to your `az login`
session, so nothing in the framework needs a CI-specific branch.

```bash
az ad sp create-for-rbac --name "fabric-cicd-agenticaidemo"
```

Keep the output. `appId`, `password` and `tenant` become the three secrets in
step 3. **The password is shown once** — if you lose it, reset the credential
rather than trying to recover it.

---

## 2. Grant it access

Two things, and the second is the one people miss.

**a. Workspace access.** The principal needs **Contributor** on each workspace
it deploys to (Admin if it must also create items):

| Environment | Workspace | Id |
|---|---|---|
| dev  | AgenticAIDemo_dev | `13c508c8-8fe8-4539-9cf4-2b4a3b91b32f` |
| qa   | AgenticAIDemo_qa  | `ca418d16-23ac-41ab-b892-eaa1cd04e268` |
| uat  | AgenticAIDemo_uat | `08f3d907-80fa-4e86-adfb-ef7d7e5fc34e` |
| prod | AgenticAIDemo     | `00e8f132-21d1-41ed-bbfc-ad3a223cf714` |

**b. The tenant setting.** In the Fabric admin portal, enable
**"Service principals can use Fabric APIs"** and include a security group
containing the principal.

Without this the API returns a plausible-looking authorisation error that does
not mention the tenant setting at all, and you will spend the afternoon
checking workspace permissions that were correct the whole time.

---

## 3. Add repository secrets

**Settings → Secrets and variables → Actions → Secrets**

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | the `appId` from step 1 |
| `AZURE_CLIENT_SECRET` | the `password` from step 1 |
| `AZURE_TENANT_ID` | the `tenant` from step 1 |

Add them at the **repository** level, or per Environment if you want prod to
use a different principal — which is worth doing if prod is a separate tenant
or subscription.

---

## 4. Create the GitHub Environments

**Settings → Environments.** Create `dev`, `qa` and `prod`, matching the
`environment:` in each workflow job.

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

## What still is not enforced

`dq-gate` is declared automated and blocks uat and prod, but no job implements
it, so nothing enforces it today. Closing that needs a runner that triggers
`nb_dq_monitor` and fails on error/critical breaches. Until then, treat the
data-quality gate as documentation rather than a control.
