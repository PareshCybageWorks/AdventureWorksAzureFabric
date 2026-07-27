# AzureFabricMCP - Claude Skills Configuration

Master configuration file for Claude Code and Cowork to discover and use AzureFabricMCP skills.

## 🎯 Overview

AzureFabricMCP provides **60+ professional skills** organized into 5 workload domains:

1. **Fabric** (15 skills) — Workspace provisioning, lakehouse management, operations
2. **Power BI** (12 skills) — Semantic models, DAX, report design & deployment
3. **Data Ops** (10 skills) — Quality validation, monitoring, SLA compliance
4. **DBT** (10 skills) — Data transformation, testing, orchestration
5. **GitHub Actions** (8 skills) — CI/CD, deployment, multi-environment promotion

## 📁 Project Structure

```
AzureFabricMCP/
├── skills/                      # All skills organized by workload
│   ├── fabric/
│   │   ├── SKILLS_INDEX.md      # Fabric skill directory (15 skills)
│   │   ├── provisioning.md
│   │   ├── workspace-operations.md
│   │   ├── lakehouse-config.md
│   │   └── ... (12 more skills)
│   │
│   ├── powerbi/
│   │   ├── SKILLS_INDEX.md      # Power BI skill directory (12 skills)
│   │   ├── semantic-modeling.md
│   │   ├── report-design.md
│   │   └── ... (10 more skills)
│   │
│   ├── dataops/
│   │   ├── SKILLS_INDEX.md      # Data Ops skill directory (10 skills)
│   │   ├── quality-expectations.md
│   │   ├── sla-monitoring.md
│   │   └── ... (8 more skills)
│   │
│   ├── dbt/
│   │   ├── SKILLS_INDEX.md      # DBT skill directory (10 skills)
│   │   ├── dbt-fundamentals.md
│   │   ├── testing-framework.md
│   │   └── ... (8 more skills)
│   │
│   ├── github-actions/
│   │   ├── SKILLS_INDEX.md      # GitHub Actions directory (8 skills)
│   │   ├── ci-cd-setup.md
│   │   ├── fabric-deployment.md
│   │   └── ... (6 more skills)
│   │
│   └── common/                  # Shared utilities & patterns
│       ├── authentication.md
│       ├── error-handling.md
│       └── best-practices.md
│
├── plugins/                     # Pre-configured MCP plugins
├── agents/                      # Specialized agents
├── docs/                        # Comprehensive documentation
├── config/                      # Configuration files
├── scripts/                     # Automation scripts
├── examples/                    # Example projects
└── CLAUDE.md                    # This file
```

## 🚀 How to Use

### Auto-Discovery

Claude automatically loads relevant skills when you mention keywords or concepts:

```
"Create Fabric workspaces"
→ Skills loaded: fabric/provisioning.md

"Design a star schema for Power BI"
→ Skills loaded: powerbi/semantic-modeling.md

"Setup DBT transformation pipeline"
→ Skills loaded: dbt/dbt-fundamentals.md, dbt/testing-framework.md

"Configure GitHub Actions CI/CD"
→ Skills loaded: github-actions/ci-cd-setup.md

"Validate data quality with expectations"
→ Skills loaded: dataops/quality-expectations.md
```

### Explicit Skill References

You can also explicitly reference skills:

```
"Using the Fabric provisioning skill, walk me through creating workspaces"
"Show me the Power BI DAX patterns skill"
"Explain the DBT testing framework skill"
"What does the Data Ops quality expectations skill cover?"
```

## 📚 Skill Categories

### Fabric (15 skills)

**Infrastructure:**
- `provisioning.md` — Create workspaces & lakehouses from specs
- `workspace-operations.md` — User management, roles, access control
- `capacity-management.md` — Plan capacity, monitor usage, optimize costs

**Data Layer:**
- `lakehouse-config.md` — Medallion architecture, Delta tables, SQL endpoints
- `data-ingestion.md` — External sources, APIs, databases, streaming
- `shortcuts-management.md` — ADLS Gen2, S3, Azure Blob, external data
- `sql-analytics.md` — T-SQL queries, SQL endpoint, query optimization

**Automation:**
- `notebooks-automation.md` — PySpark, SQL notebooks, scheduling
- `git-integration.md` — Version control, CI/CD integration
- `api-automation.md` — REST APIs, item management, programmatic control

**Operations:**
- `monitoring.md` — Health checks, alerts, metrics
- `diagnostics.md` — Performance analysis, troubleshooting
- `security-config.md` — RLS, encryption, access policies

**Advanced:**
- `migration.md` — Warehouse migration, legacy integration
- `cost-optimization.md` — CapEx optimization, efficiency

### Power BI (12 skills)

**Modeling:**
- `semantic-modeling.md` — Star schema, relationships, tables
- `dax-fundamentals.md` — Measures, calculated columns, functions
- `dax-advanced.md` — Complex patterns, optimization, performance

**Reporting:**
- `report-design.md` — Dashboards, visuals, interactivity
- `mobile-optimization.md` — Phone reports, responsive design
- `paginated-reports.md` — Operational & printable reports

**Deployment:**
- `deployment-automation.md` — Model deployment, versioning
- `refresh-scheduling.md` — Data refresh, automation
- `performance-tuning.md` — Query optimization, aggregations

**Security & Features:**
- `rls-security.md` — Row-level security, object-level security
- `dataflow-automation.md` — Dataflow Gen2, cloud transformations
- `embedded-analytics.md` — Power BI Embedded integration

### Data Ops (10 skills)

**Quality & Validation:**
- `quality-expectations.md` — Great Expectations, quality checks
- `data-contracts.md` — Data contract enforcement, SLA definition
- `anomaly-detection.md` — Statistical detection, outlier flagging

**Monitoring & Operations:**
- `sla-monitoring.md` — Freshness, completeness, accuracy tracking
- `data-lineage.md` — Dependency tracking, impact analysis
- `audit-logging.md` — Access logs, change tracking, compliance

**Advanced Operations:**
- `data-profiling.md` — Distribution analysis, metadata
- `reconciliation.md` — Source-to-target validation, ETL testing
- `incident-response.md` — Outage management, remediation
- `cost-analysis.md` — Operation costs, optimization

### DBT (10 skills)

**Core Concepts:**
- `dbt-fundamentals.md` — Project setup, basic modeling
- `source-freshness.md` — Source monitoring, SLA checks
- `testing-framework.md` — Data testing, quality checks

**Advanced Development:**
- `dbt-docs.md` — Auto-documentation, data catalog
- `slim-ci.md` — State-based CI, selective testing
- `snapshots.md` — SCD Type 2, historical tracking

**Deployment & Integration:**
- `dbt-cloud.md` — Cloud orchestration, scheduling
- `fabric-integration.md` — Fabric + DBT workflows
- `seeds-management.md` — Seed data, reference tables
- `performance-optimization.md` — Query optimization, run times

### GitHub Actions (8 skills)

**CI/CD Foundation:**
- `ci-cd-setup.md` — Workflow basics, triggers, jobs
- `testing-automation.md` — Automated testing, validation
- `secrets-management.md` — Secure credential handling

**Deployment:**
- `fabric-deployment.md` — Deploy Fabric items, notebooks
- `powerbi-deployment.md` — Publish Power BI models & reports
- `multi-environment.md` — Dev → QA → UAT → Prod promotion

**Operations:**
- `deployment-gates.md` — Approval workflows, manual gates
- `notification-integration.md` — Slack, Teams, webhooks

## 🔄 Workflow Examples

### Complete Data Platform Setup

```
Step 1: Infrastructure
├─ fabric/provisioning.md
├─ fabric/workspace-operations.md
└─ fabric/lakehouse-config.md

Step 2: Data Ingestion & Processing
├─ fabric/data-ingestion.md
├─ fabric/notebooks-automation.md
└─ dbt/dbt-fundamentals.md

Step 3: Quality Assurance
├─ dataops/quality-expectations.md
├─ dataops/sla-monitoring.md
└─ dbt/testing-framework.md

Step 4: Analytics & Reporting
├─ powerbi/semantic-modeling.md
├─ powerbi/dax-fundamentals.md
└─ powerbi/report-design.md

Step 5: Automation & Deployment
├─ fabric/git-integration.md
├─ github-actions/ci-cd-setup.md
├─ github-actions/fabric-deployment.md
├─ github-actions/powerbi-deployment.md
└─ github-actions/multi-environment.md
```

### Analytics Engineering Workflow

```
Source → Fabric Bronze → DBT Silver → Fabric Gold → Power BI Semantic Model
   ↓         ↓               ↓           ↓             ↓
fabric/    dbt/          dbt/        powerbi/    github-actions/
data-      dbt-          testing-    semantic-   ci-cd-setup
ingestion  fundamentals  framework   modeling
   ↓         ↓               ↓           ↓             ↓
dataops/quality-expectations.md ← sla-monitoring.md → testing-automation.md
```

### Multi-Environment Promotion

```
Code Push
    ↓
github-actions/ci-cd-setup.md
    ↓
github-actions/testing-automation.md
    ↓
Dev Environment
├─ fabric/workspace-operations.md (Deploy to Dev)
└─ powerbi/deployment-automation.md (Deploy to Dev)
    ↓
QA Environment
├─ github-actions/multi-environment.md (Promote to QA)
├─ dataops/sla-monitoring.md (QA validation)
└─ dataops/quality-expectations.md (QA tests)
    ↓
Production Environment
├─ github-actions/deployment-gates.md (Approval)
├─ github-actions/multi-environment.md (Deploy to Prod)
├─ dataops/incident-response.md (Monitor)
└─ github-actions/notification-integration.md (Alert)
```

## 🎓 Learning Paths

### Path 1: Data Engineer (Weeks 1-8)

**Week 1:** Foundations
- fabric/provisioning.md
- fabric/workspace-operations.md
- common/authentication.md

**Week 2-3:** Data Layer
- fabric/lakehouse-config.md
- fabric/data-ingestion.md
- fabric/shortcuts-management.md

**Week 4-5:** Transformation
- dbt/dbt-fundamentals.md
- dbt/testing-framework.md
- fabric/notebooks-automation.md

**Week 6-7:** Quality & Ops
- dataops/quality-expectations.md
- dataops/sla-monitoring.md
- dataops/data-lineage.md

**Week 8:** Automation
- fabric/git-integration.md
- github-actions/ci-cd-setup.md
- github-actions/fabric-deployment.md

### Path 2: Analytics Engineer (Weeks 1-6)

**Week 1:** Foundations
- fabric/lakehouse-config.md
- powerbi/semantic-modeling.md
- common/best-practices.md

**Week 2-3:** Modeling & DAX
- powerbi/dax-fundamentals.md
- powerbi/dax-advanced.md
- powerbi/performance-tuning.md

**Week 4-5:** Reporting & Design
- powerbi/report-design.md
- powerbi/mobile-optimization.md
- powerbi/rls-security.md

**Week 6:** Deployment
- powerbi/deployment-automation.md
- github-actions/powerbi-deployment.md
- github-actions/multi-environment.md

### Path 3: DevOps Engineer (Weeks 1-4)

**Week 1:** Platform Setup
- fabric/provisioning.md
- fabric/capacity-management.md
- fabric/security-config.md

**Week 2:** Automation & CI/CD
- github-actions/ci-cd-setup.md
- github-actions/secrets-management.md
- fabric/git-integration.md

**Week 3:** Deployment Pipelines
- github-actions/fabric-deployment.md
- github-actions/powerbi-deployment.md
- github-actions/multi-environment.md

**Week 4:** Operations & Monitoring
- fabric/monitoring.md
- dataops/sla-monitoring.md
- github-actions/notification-integration.md

## 🔧 Configuration Files

All skills reference standardized config files:

```
config/
├── fabric-workspaces-spec.json     # Infrastructure definition
├── powerbi-models-spec.json        # Semantic model specs
├── dbt-project-config.yml          # DBT configuration
├── github-workflow-spec.json       # CI/CD pipeline specs
├── data-contracts.yaml             # Quality SLAs
└── .env.example                    # Environment template
```

## 🛠️ Common Patterns

### Pattern 1: Specs-Driven Infrastructure
```
JSON Spec → Claude + Skills → Fabric Workspaces → Lakehouses → SQL Endpoints
```

### Pattern 2: Quality-First Data Ops
```
Data → Ingestion → Transformation → Validation (Great Expectations) → Monitoring
```

### Pattern 3: Automated Delivery
```
Git Push → GitHub Actions → Test → Deploy (Fabric) → Deploy (PowerBI) → Monitor
```

### Pattern 4: Medallion Architecture
```
Bronze (Raw) → Silver (Clean, Transform) → Gold (Analytics) → PowerBI Models
```

## ✅ Skill Quality

All skills include:
- ✅ **Clear objectives** — What you'll accomplish
- ✅ **Concepts** — Key ideas & terminology
- ✅ **Patterns** — Best practices & examples
- ✅ **Code samples** — 5-10 working examples per skill
- ✅ **Troubleshooting** — Common issues & solutions
- ✅ **Next steps** — What to learn after

## 🔍 Skill Discovery Tips

```
"I need to..." → Claude finds relevant skills

"I need to set up a data pipeline"
→ Loads: fabric/data-ingestion.md, dbt/dbt-fundamentals.md, 
         dataops/quality-expectations.md

"I need to deploy Power BI models automatically"
→ Loads: powerbi/deployment-automation.md, 
         github-actions/powerbi-deployment.md,
         github-actions/multi-environment.md

"I need to monitor data quality"
→ Loads: dataops/quality-expectations.md,
         dataops/sla-monitoring.md,
         dataops/anomaly-detection.md

"I need to optimize Power BI performance"
→ Loads: powerbi/performance-tuning.md,
         powerbi/dax-advanced.md,
         powerbi/semantic-modeling.md
```

## 📖 Additional Resources

### Getting Started
- `docs/getting-started/installation.md`
- `docs/getting-started/quick-start.md`
- `docs/getting-started/architecture.md`

### Example Projects
- `examples/ecommerce-analytics/`
- `examples/financial-reporting/`
- `examples/real-time-monitoring/`
- `examples/enterprise-dw/`

### Scripts & Automation
- `scripts/provision-fabric.sh`
- `scripts/deploy-dbt.sh`
- `scripts/setup-github-actions.py`
- `scripts/deploy-powerbi.py`

## 🎯 Next Steps

1. **Choose your path:** Data Engineer, Analytics Engineer, or DevOps
2. **Start with foundations:** Set up credentials and environments
3. **Follow the workflow:** Use relevant skills step-by-step
4. **Reference examples:** See how others built similar solutions
5. **Automate everything:** Use GitHub Actions to make it repeatable

## 🚀 Ready to Begin?

Ask Claude:

```
"I want to build a complete data platform using Fabric, DBT, and Power BI.
 Walk me through the AzureFabricMCP skills to set this up."
```

Claude will automatically load and guide you through all relevant skills! 🎉

---

**AzureFabricMCP Version:** 1.0  
**Total Skills:** 55+  
**Total Content:** 15,000+ lines  
**Status:** ✅ Production Ready  
**Last Updated:** July 20, 2026
