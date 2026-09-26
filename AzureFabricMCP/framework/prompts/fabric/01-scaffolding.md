# Stage F1 — Fabric Scaffolding

**Track:** Fabric  **Stage:** 1 of 5  **Produces:** `fabric/01-scaffolding.yaml`
**Contract:** `framework/contracts/fabric/01-scaffolding.schema.json` (authoritative)

---

## Purpose

Define the platform the project runs **on** — tenant, capacity, topology, domains,
environment workspaces, storage items, naming. Nothing about *data* belongs here.

Everything downstream resolves names and placement from this spec, so a value
that is wrong here is wrong in every generated notebook, pipeline and report.

## When to use

- Starting a new Fabric project
- Adding an environment or a domain to an existing one
- Switching topology between medallion and mesh

## Preconditions

Refuse to proceed and say why if any of these is unmet:

- [ ] A Fabric capacity exists and its SKU is known
- [ ] The operator can name the target tenant/region
- [ ] `az login` is active, or a service principal is available

---

## Step 1 — Ask, do not assume

Five answers cannot be inferred from a codebase. Ask them; never default silently.

**1. Topology — medallion or mesh?**

Do not just take the answer. Test it:

> "Does more than one team own a pipeline end to end, with independent release
> cadences?"

Mesh pays for its overhead only when **all** of: multiple teams, independent
release cadences, and a platform-team bottleneck that decentralising would
relieve. One team building a first framework meets none. Recommend medallion,
and record the reasoning in `topology.rationale` — the contract requires it, and
enforces a minimum length, because an undocumented topology choice gets
re-litigated by every new joiner.

**2. Which capacity, and is it a trial?**

Get the SKU, not just the name. **An F2 has 2 capacity units and cannot run
Spark at any useful speed** — a pipeline lands on it and simply crawls, with no
error to explain why. If the answer is a trial, set `is_trial: true`: a trial
expires and takes *every* environment's compute with it at the same moment.

Never infer capacity from "whatever workspace we saw first".

**3. Which environments, and what is production called?**

Production frequently carries the *shortest* name of the set — `AgenticAIDemo`
beside `AgenticAIDemo_dev`. That makes it the easiest to hit by accident. Set
`is_production: true` on it, and make sure the operator knows every destructive
operation must resolve that workspace **by id, never by name**.

**4. What are the domains?**

Ask even when the answer is "just one". `domain` must be a first-class field
from day one — without it, switching to mesh later means rewriting every spec
instead of changing one line.

**5. Where does gold live — Warehouse or Lakehouse?**

Choose Warehouse (`wh_gold`) when gold needs real T-SQL schemas, views, or a SQL
surface for BI tools. Choose Lakehouse only when gold is consumed exclusively by
Spark. That is rarer than it sounds — Power BI, Excel and most BI tools want SQL.

---

## Step 2 — Derive what you can

Do not ask about these. Derive them and state what you chose:

| Field | Rule |
|---|---|
| `storage.items` | Follows topology. medallion → per LAYER (`lh_bronze`, `lh_silver`, `wh_gold`). mesh → per DOMAIN (`wh_{domain}`). The split follows whichever axis owns the data. |
| `naming.tables.silver` | `stg_{entity}`. Silver holds cleansed **staging**, not a dimensional model. |
| `naming.tables.gold_*` | `dim_{entity}` / `fct_{entity}`. |
| `naming.verbs` | `load` / `clean` / `build`. The layer is already carried by the storage item and the folder; repeating it yields `nb_bronze_bronze_commerce_orders`. |
| `workspace_folders` | `0_config` … `6_powerbi`, each layer folder splitting into `notebook/` and `pipeline/`. Numeric prefixes force medallion order in an alphabetical UI. Take the template's list as-is: it declares the four items that match no layer pattern — `nb_dq_monitor`, `nb_cascade_quarantine`, the semantic model and the report. Drop any of them and `organise_items` leaves it at the workspace root while reporting `0 unmatched`. `folder-coverage` fails the build first. |

## Step 3 — Write the spec

Emit `fabric/01-scaffolding.yaml` conforming to the contract. Copy
`framework/templates/fabric/01-scaffolding.yaml` as the starting point.

Secrets are **references**, never values. The contract rejects anything that
looks like a literal secret.

Default to `${ENV_VAR}` — it is resolved first and needs nobody's permission:
CI injects it from repository secrets and a local run reads the shell.
`keyvault://vault/secret-name` is supported and is the better durable store,
but granting a principal access to it needs `roleAssignments/write`, which a
Contributor does not hold and a tenant forcing RBAC gives no way around. Choose
it once someone has confirmed they can actually grant the role, rather than
inheriting it and discovering that later.

## Step 4 — Validate

```bash
python AzureFabricMCP/framework/generators/validate.py --project <project> --track fabric
```

Authoritative: a non-conforming spec **fails**. Do not proceed to provisioning
with validation errors outstanding.

## Step 5 — Provision

```bash
python AzureFabricMCP/framework/deploy/create_workspaces.py --project <project> --capacity <id>
python AzureFabricMCP/framework/deploy/provision_storage.py --project <project> --env dev
```

**Then, create workspace folders manually in Fabric UI** (~2 minutes):

Fabric workspace folders are a UI-only feature and cannot be created
programmatically. Using the Fabric Admin Portal, create the folder structure
defined in `workspace_folders`:

- `0_config/notebook/`
- `1_bronze/notebook/`, `1_bronze/pipeline/`
- `2_silver/notebook/`, `2_silver/pipeline/`
- `3_gold/notebook/`, `3_gold/pipeline/`
- `4_master/pipeline/`
- `5_datastore/`
- `6_powerbi/semanticmodel/`, `6_powerbi/report/`

Then continue:

```bash
python AzureFabricMCP/framework/deploy/organise_items.py   --project <project> --env dev
```

Record the resulting ids back into `environments[].provisioned_items`, so the
spec and the tenant agree. An id recorded nowhere gets rediscovered by hand
every time.

---

## Step 6 — Git mirroring (optional)

Mirrors deployed items into the repo as text, so "what changed between Tuesday
and Friday" is a commit range instead of somebody's memory.

```bash
python AzureFabricMCP/framework/deploy/connect_git.py --project <project> --env dev --status
python AzureFabricMCP/framework/deploy/connect_git.py --project <project> --env dev
```

**It is a mirror, not a deployment path.** Items reach a workspace one way:
`specs → generated/ → push_items.py`. The sync only commits outward, and
`direction` has exactly one legal value for that reason — an inward sync would
compete with `push_items.py` over the same items, and both report success, so
whichever lost would do so silently.

State the consequence to the user rather than leaving it implied: an item
edited in the Fabric UI gets committed here and then sits in the repository
looking authoritative while disagreeing with the spec that generated it.
`no-drift` compares specs against `generated/` and cannot see it.

Two prerequisites no script can satisfy:

- a **tenant admin** enables *Admin portal → Tenant settings → Users can
  synchronize workspace items with their GitHub repositories*. Until then every
  call returns `FeatureNotAvailable`, whatever the spec says.
- a **Fabric connection** holds a GitHub PAT with `repo` scope. Only its id
  belongs in the spec. Azure DevOps needs neither — it authenticates as the
  calling user, which is worth knowing before choosing a provider.

The two providers also take different **fields**, not merely different
credentials:

| Provider | `repository` |
|---|---|
| GitHub | `<owner>/<repo>` |
| Azure DevOps | `<organisation>/<project>/<repo>` |

Azure DevOps addresses a repo by organisation *and* project; the project is a
separate level and cannot be inferred from the other two. Getting it wrong is
rejected by the API as a missing field, which reads like a malformed request
rather than the wrong shape for the provider — so validation checks the part
count against the provider instead.

Mirror `dev` only unless there is a reason not to. Promoted environments
receive the same artefacts, so mirroring them records the same thing again
under another branch.

**The `directory` must already exist on that branch.** Fabric *resolves* it as
a resource rather than creating it, and a missing directory returns
`GitProviderResourceNotFound` — an error that names neither the directory nor
the branch, so it reads like a wrong repository name. Create it first; a README
or `.gitkeep` is enough.

Connecting with `directory: /` succeeds, which is the trap: it hands Fabric the
whole repository root, and the first sync writes every item beside whatever was
already there. Never use it to get past the error above.

Expect **fewer items in git than in the workspace**. SQL endpoints are
auto-created children of a lakehouse or warehouse and are not independently
syncable — a workspace of 28 items mirrors as 26. That is correct, not a
partial sync. Fabric preserves workspace folders, so the mirror comes out
organised the way `organise_items.py` left it.

---

## Exit gate

Stage F1 is done when **all** hold:

- [ ] `validate.py` passes with zero errors
- [ ] Every environment has a real `workspace_id`
- [ ] Storage items exist and match `storage.items` for the active topology
- [ ] Items are filed per `workspace_folders`
- [ ] `topology.rationale` explains the choice in words
- [ ] Production is marked `is_production: true`

Only then proceed to **F2 — Sources**.

---

## Failure modes seen in practice

Each of these cost real debugging time. They are listed because none announced
itself as an error.

**Capacity picked by accident.** Defaulting to "the first workspace's capacity"
silently selected an F2. Spark ran, slowly, with nothing to indicate why. Derive
capacity explicitly or refuse to guess.

**Workspace folders cannot be created programmatically.** Fabric workspace
folders are a UI-only feature; attempting to create items of type "Folder" via
API returns `InvalidItemType`. Every item is born at the workspace root until
folders exist. Folders must be created manually in Fabric UI before running
`organise_items.py`. Without pre-existing folders, `organise_items` leaves items
at root and reports "0 unmatched" (items not planned for were never planned for),
which masks misalignment between spec and workspace.

**Gold written to the silver lakehouse.** A notebook's default lakehouse cannot
be a Warehouse, so gold notebooks default to the lakehouse they *read* from —
and an unqualified `saveAsTable` lands there. Gold tables ended up beside silver
under names one character apart (`dim_customer` vs `dim_customers`). Route every
gold write through the configured target; never write unqualified.

**The warehouse connector supports overwrite only.** `append` fails with
`Write orchestration failed` and no root cause. Read, union, overwrite.

**A workspace name is not an identity.** Names get renamed; ids do not. Record
ids and resolve destructive operations by id.

---

## Related

- `framework/skills/fabric/lakehouse-warehouse-topology.md` — the medallion vs mesh decision in depth
- `framework/contracts/fabric/01-scaffolding.schema.json` — the authoritative shape
- Next stage: `framework/prompts/fabric/02-sources.md`
