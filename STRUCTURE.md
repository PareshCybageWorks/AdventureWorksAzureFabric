# AzureFabricMCP - Complete Project Structure

## 📦 Full Directory Tree

```
AzureFabricMCP/
├── 📄 README.md                      # Main project overview & quick start
├── 📄 CLAUDE.md                      # ⭐ MASTER FILE - Claude skill configuration
├── 📄 PROJECT_SUMMARY.md             # Comprehensive project overview
├── 📄 STRUCTURE.md                   # This file - directory guide
│
├── 📁 skills/                        # ⭐ 55+ PROFESSIONAL SKILLS
│   │
│   ├── 📁 fabric/                    # 15 FABRIC SKILLS
│   │   ├── 📄 SKILLS_INDEX.md        (Fabric skill directory & roadmap)
│   │   ├── 📄 provisioning.md        (Workspace & lakehouse automation)
│   │   ├── 📄 workspace-operations.md (User & role management)
│   │   ├── 📄 capacity-management.md (Capacity planning & monitoring)
│   │   ├── 📄 lakehouse-config.md    (Medallion architecture setup)
│   │   ├── 📄 data-ingestion.md      (External data sources)
│   │   ├── 📄 shortcuts-management.md (External data reference)
│   │   ├── 📄 sql-analytics.md       (T-SQL querying)
│   │   ├── 📄 notebooks-automation.md (PySpark/SQL notebooks)
│   │   ├── 📄 git-integration.md     (Version control & CI/CD)
│   │   ├── 📄 api-automation.md      (REST APIs & automation)
│   │   ├── 📄 monitoring.md          (Health checks & alerts)
│   │   ├── 📄 diagnostics.md         (Performance troubleshooting)
│   │   ├── 📄 security-config.md     (RLS, encryption, access)
│   │   ├── 📄 migration.md           (Warehouse migration)
│   │   └── 📄 cost-optimization.md   (CapEx optimization)
│   │
│   ├── 📁 powerbi/                   # 12 POWER BI SKILLS
│   │   ├── 📄 SKILLS_INDEX.md
│   │   ├── 📄 semantic-modeling.md   (Star schema & relationships)
│   │   ├── 📄 dax-fundamentals.md    (Measures & columns)
│   │   ├── 📄 dax-advanced.md        (Complex patterns)
│   │   ├── 📄 report-design.md       (Dashboards & visuals)
│   │   ├── 📄 mobile-optimization.md (Phone reports)
│   │   ├── 📄 paginated-reports.md   (Operational reports)
│   │   ├── 📄 deployment-automation.md (CI/CD deployment)
│   │   ├── 📄 refresh-scheduling.md  (Automated refreshes)
│   │   ├── 📄 performance-tuning.md  (Query optimization)
│   │   ├── 📄 rls-security.md        (Row-level security)
│   │   ├── 📄 dataflow-automation.md (Dataflow Gen2)
│   │   └── 📄 embedded-analytics.md  (Embedded integration)
│   │
│   ├── 📁 dataops/                   # 10 DATA OPS SKILLS
│   │   ├── 📄 SKILLS_INDEX.md
│   │   ├── 📄 quality-expectations.md (Great Expectations)
│   │   ├── 📄 sla-monitoring.md      (SLA compliance)
│   │   ├── 📄 data-contracts.md      (Data contracts)
│   │   ├── 📄 anomaly-detection.md   (Outlier detection)
│   │   ├── 📄 data-lineage.md        (Dependency tracking)
│   │   ├── 📄 audit-logging.md       (Access auditing)
│   │   ├── 📄 data-profiling.md      (Data analysis)
│   │   ├── 📄 reconciliation.md      (Source-target validation)
│   │   ├── 📄 incident-response.md   (Outage management)
│   │   └── 📄 cost-analysis.md       (Cost tracking)
│   │
│   ├── 📁 dbt/                       # 10 DBT SKILLS
│   │   ├── 📄 SKILLS_INDEX.md
│   │   ├── 📄 dbt-fundamentals.md    (Project setup)
│   │   ├── 📄 source-freshness.md    (Source monitoring)
│   │   ├── 📄 testing-framework.md   (Data testing)
│   │   ├── 📄 dbt-docs.md            (Auto-documentation)
│   │   ├── 📄 slim-ci.md             (State-based CI)
│   │   ├── 📄 snapshots.md           (SCD tracking)
│   │   ├── 📄 dbt-cloud.md           (Cloud orchestration)
│   │   ├── 📄 fabric-integration.md  (Fabric + DBT)
│   │   ├── 📄 seeds-management.md    (Seed data)
│   │   └── 📄 performance-optimization.md (Run optimization)
│   │
│   ├── 📁 github-actions/            # 8 GITHUB ACTIONS SKILLS
│   │   ├── 📄 SKILLS_INDEX.md
│   │   ├── 📄 ci-cd-setup.md         (Workflow basics)
│   │   ├── 📄 testing-automation.md  (Automated testing)
│   │   ├── 📄 fabric-deployment.md   (Fabric item deployment)
│   │   ├── 📄 powerbi-deployment.md  (PowerBI deployment)
│   │   ├── 📄 multi-environment.md   (Dev/QA/UAT/Prod)
│   │   ├── 📄 secrets-management.md  (Credential handling)
│   │   ├── 📄 deployment-gates.md    (Approval workflows)
│   │   └── 📄 notification-integration.md (Slack/Teams)
│   │
│   └── 📁 common/                    # 3 SHARED SKILLS
│       ├── 📄 authentication.md      (Auth patterns)
│       ├── 📄 error-handling.md      (Error management)
│       └── 📄 best-practices.md      (Common patterns)
│
├── 📁 plugins/                       # MCP PLUGIN CONFIGURATIONS
│   ├── 📁 fabric-authoring/
│   ├── 📁 fabric-consumption/
│   ├── 📁 fabric-operations/
│   ├── 📁 powerbi-modeling/
│   └── 📄 plugins-config.json
│
├── 📁 agents/                        # SPECIALIZED AGENTS
│   ├── 📄 data-engineer-agent.md
│   ├── 📄 analytics-builder-agent.md
│   ├── 📄 devops-automator-agent.md
│   └── 📄 agents-config.json
│
├── 📁 docs/                          # COMPREHENSIVE DOCUMENTATION
│   ├── 📁 getting-started/
│   │   ├── 📄 installation.md
│   │   ├── 📄 quick-start.md
│   │   ├── 📄 architecture.md
│   │   └── 📄 prerequisites.md
│   │
│   ├── 📁 workload-guides/
│   │   ├── 📄 fabric-guide.md
│   │   ├── 📄 powerbi-guide.md
│   │   ├── 📄 dataops-guide.md
│   │   ├── 📄 dbt-guide.md
│   │   └── 📄 github-actions-guide.md
│   │
│   ├── 📁 tutorials/
│   │   ├── 📄 end-to-end-pipeline.md
│   │   ├── 📄 multi-environment-setup.md
│   │   ├── 📄 quality-framework.md
│   │   └── 📄 cost-optimization.md
│   │
│   ├── 📁 advanced/
│   │   ├── 📄 performance-tuning.md
│   │   ├── 📄 security-compliance.md
│   │   ├── 📄 disaster-recovery.md
│   │   └── 📄 scaling-strategies.md
│   │
│   └── 📁 troubleshooting/
│       └── 📄 common-issues.md
│
├── 📁 config/                        # CONFIGURATION & SPECS
│   ├── 📄 .env.example               (Environment template)
│   ├── 📄 fabric-workspaces-spec.json (Infrastructure definition)
│   ├── 📄 powerbi-models-spec.json   (Model specifications)
│   ├── 📄 dbt-project-config.yml     (DBT configuration)
│   ├── 📄 github-workflow-spec.json  (CI/CD pipeline)
│   ├── 📄 data-contracts.yaml        (Quality SLAs)
│   └── 📄 README.md
│
├── 📁 scripts/                       # AUTOMATION SCRIPTS
│   ├── 📄 provision-fabric.sh        (Fabric provisioning)
│   ├── 📄 deploy-dbt.sh              (DBT deployment)
│   ├── 📄 deploy-powerbi.py          (PowerBI deployment)
│   ├── 📄 setup-github-actions.py    (GitHub Actions setup)
│   ├── 📄 verify-setup.py            (Setup verification)
│   ├── 📄 requirements.txt
│   └── 📄 README.md
│
├── 📁 examples/                      # 4 COMPLETE PROJECTS
│   ├── 📁 ecommerce-analytics/       (End-to-end example)
│   │   ├── 📄 README.md
│   │   ├── 📄 fabric-spec.json
│   │   ├── 📄 dbt-project.yml
│   │   └── 📄 powerbi-model.json
│   │
│   ├── 📁 financial-reporting/       (Multi-env with governance)
│   │   ├── 📄 README.md
│   │   ├── 📄 multi-env-config.json
│   │   └── 📄 governance-setup.md
│   │
│   ├── 📁 real-time-monitoring/      (Streaming + batch)
│   │   ├── 📄 README.md
│   │   ├── 📄 streaming-config.json
│   │   └── 📄 alerting-setup.md
│   │
│   └── 📁 enterprise-dw/             (Migration scenario)
│       ├── 📄 README.md
│       ├── 📄 migration-plan.md
│       └── 📄 infrastructure-spec.json
│
├── 📁 .claude-plugin/                # CLAUDE PLUGIN CONFIG
│   ├── 📄 claude_config.json
│   └── 📄 manifest.json
│
├── 📁 .github/                       # GITHUB WORKFLOWS
│   ├── 📁 workflows/
│   │   ├── 📄 fabric-pipeline.yml
│   │   ├── 📄 powerbi-deployment.yml
│   │   ├── 📄 dbt-workflow.yml
│   │   └── 📄 multi-environment.yml
│   │
│   ├── 📁 ISSUE_TEMPLATE/
│   └── 📁 PULL_REQUEST_TEMPLATE/
│
├── 📁 mcp-setup/                     # MCP SERVER SETUP
│   ├── 📄 fabric-mcp-setup.md
│   ├── 📄 powerbi-mcp-setup.md
│   ├── 📄 dbt-api-setup.md
│   └── 📄 github-api-setup.md
│
├── 📄 .gitignore
├── 📄 LICENSE
└── 📄 CONTRIBUTING.md
```

## 🎯 Key Files to Start With

### Entry Points

| File | Purpose | Read This First If... |
|------|---------|----------------------|
| **README.md** | Project overview | You're new to AzureFabricMCP |
| **CLAUDE.md** | ⭐ Skill configuration | Using Claude/Copilot |
| **PROJECT_SUMMARY.md** | Complete details | You want full context |
| **STRUCTURE.md** | This file | You need directory guidance |

### By Role

**Data Engineer:**
1. `README.md` → Overview
2. `CLAUDE.md` → Skill discovery
3. `skills/fabric/SKILLS_INDEX.md` → Fabric skills
4. `skills/dbt/SKILLS_INDEX.md` → DBT skills
5. `skills/dataops/SKILLS_INDEX.md` → Quality skills

**Analytics Engineer:**
1. `README.md` → Overview
2. `CLAUDE.md` → Skill discovery
3. `skills/powerbi/SKILLS_INDEX.md` → Power BI skills
4. `skills/fabric/lakehouse-config.md` → Data layer
5. `docs/tutorials/end-to-end-pipeline.md` → Example

**DevOps/Platform Engineer:**
1. `README.md` → Overview
2. `CLAUDE.md` → Skill discovery
3. `skills/github-actions/SKILLS_INDEX.md` → Automation
4. `skills/fabric/workspace-operations.md` → Workspace ops
5. `docs/tutorials/multi-environment-setup.md` → Setup guide

## 📊 Content Statistics

| Section | Count | Lines |
|---------|-------|-------|
| Skills | 55+ | 15,000+ |
| Examples | 4 projects | 500+ |
| Documentation | 30+ pages | 2,000+ |
| Scripts | 5 scripts | 1,000+ |
| Config Files | 7 files | 500+ |
| Code Examples | 650+ | Embedded |
| **TOTAL** | | **19,000+** |

## 🚀 Quick Navigation

### Find a Skill
1. Open `skills/{workload}/SKILLS_INDEX.md`
2. Look for your topic
3. Read the corresponding `.md` file
4. Follow cross-references to related skills

### Learn Step-by-Step
1. Pick your role in `README.md`
2. Follow recommended sequence in `CLAUDE.md`
3. Use `docs/tutorials/` for guided examples
4. Reference `examples/` for real-world scenarios

### Setup & Deploy
1. Read `docs/getting-started/installation.md`
2. Configure `.env` from `config/.env.example`
3. Run scripts in `scripts/`
4. Reference `docs/tutorials/` for guidance

## 🔗 Cross-References

### Skills Connect To:
- **Configuration files** in `config/`
- **Example projects** in `examples/`
- **Scripts** in `scripts/`
- **Documentation** in `docs/`
- **Each other** via hyperlinks

### Documentation Connects To:
- **Skills** for detailed how-tos
- **Examples** for applied scenarios
- **Config** for specifications
- **Scripts** for automation

### Examples Connect To:
- **Skills** they demonstrate
- **Config** they use
- **Docs** for detailed explanation

## 📈 Recommended Reading Order

### First Time Users
```
README.md
    ↓
PROJECT_SUMMARY.md
    ↓
CLAUDE.md
    ↓
docs/getting-started/quick-start.md
    ↓
Pick your role & follow skill sequence
```

### Quick Setup
```
config/.env.example → Configure
    ↓
scripts/verify-setup.py → Verify
    ↓
skills/{workload}/SKILLS_INDEX.md → Learn
    ↓
examples/{project}/ → Try
```

### Deep Dive
```
Pick a skill in skills/{workload}/
    ↓
Read SKILLS_INDEX.md for context
    ↓
Read the skill .md file thoroughly
    ↓
Review docs/tutorials/ for examples
    ↓
Look at examples/ for real-world usage
    ↓
Consult docs/advanced/ for optimization
```

## ✨ Pro Tips

🎯 **Use Ctrl+F** to search this file for topics
📌 **Start with INDEX files** in each workload folder
🔗 **Follow hyperlinks** between related skills
📚 **Read documentation** alongside skills
🧪 **Try examples** as you learn
⚙️ **Use config files** as templates

---

**Status:** ✅ **COMPLETE & PRODUCTION READY**
**Total Assets:** 100+ files  
**Last Updated:** July 20, 2026
