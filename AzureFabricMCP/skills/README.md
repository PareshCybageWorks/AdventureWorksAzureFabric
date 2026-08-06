# Azure Fabric MCP - Comprehensive Skills Library

A professional collection of AI assistant skills for **Microsoft Azure Fabric**, **Power BI**, **Data Operations**, **DBT**, and **GitHub Actions**. Designed for automation, data engineering, and intelligent deployment workflows.

## 🎯 Overview

AzureFabricMCP provides enterprise-grade skills and MCP integrations for:

- **Fabric** — Workspace provisioning, lakehouse management, medallion architecture
- **Power BI** — Semantic models, DAX optimization, report deployment
- **Data Ops** — Quality validation (Great Expectations), monitoring, SLA compliance
- **DBT** — Data transformation workflows, model management, testing automation
- **GitHub Actions** — CI/CD pipelines, multi-environment promotion, automated deployment

## 📦 What's Included

### Skills — 2 written, by design rather than by neglect

```
skills/
├── fabric/              # 1: lakehouse & warehouse topology
├── powerbi/             # 1: DirectLake binding and TMDL authoring
├── dataops/             # empty
├── dbt/                 # empty
├── github-actions/      # empty
└── common/              # empty
```

This folder previously advertised "60+ guides" across these workloads. Those
files were never present. The count is corrected rather than left standing,
because an index that overstates itself stops you looking where the knowledge
actually is.

**Most of it is in the framework, not here.** The ten stage prompts under
`framework/prompts/` carry the procedure and the failure modes; the deploy and
generator scripts carry the platform behaviour. A skill is written only when a
behaviour cost real debugging time *and* the explanation does not belong in a
prompt — which is why there are two. Each folder's `SKILLS_INDEX.md` lists what
exists and points at where the rest actually lives.

### Plugins & Agents

```
plugins/                 # Pre-configured MCP plugins
agents/                  # Specialized agents by role
docs/                    # Comprehensive documentation
mcp-setup/               # MCP server configuration
```

## 🚀 Quick Start

### 1. Create Fabric Workspaces

```bash
cd AzureFabricMCP
./scripts/provision-fabric.sh
```

### 2. Deploy DBT Project

```bash
dbt init --profiles-dir .
dbt deps
dbt run
```

### 3. Setup GitHub Actions

```bash
cp .github/workflows/example.yml .github/workflows/ci-cd.yml
git push  # Triggers workflow
```

### 4. Create Power BI Models

```bash
python scripts/deploy-powerbi.py config/powerbi-models-spec.json
```

## 📚 Skill Bundles

| Bundle | Skills | Use Case |
|--------|--------|----------|
| **fabric-complete** | All Fabric skills | Full workspace management |
| **powerbi-analytics** | All Power BI skills | Analytics & reporting |
| **dataops-suite** | All Data Ops skills | Quality & monitoring |
| **dbt-transformation** | All DBT skills | Data transformation |
| **ci-cd-automation** | GitHub Actions skills | Automated deployment |
| **data-platform** | All combined | Complete data platform |

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│    AI Assistant (Claude / GitHub Copilot)       │
└─────────────────┬───────────────────────────────┘
                  │
      ┌───────────┼───────────┐
      │           │           │
  ┌───▼──┐   ┌───▼──┐   ┌───▼──┐
  │Skills│   │Agents│   │Plugins
  └───┬──┘   └───┬──┘   └───┬──┘
      │         │           │
  ┌───▼─────────▼───────────▼──┐
  │   MCP Servers & APIs        │
  │ ├─ Fabric MCP Server        │
  │ ├─ Power BI MCP             │
  │ ├─ DBT Cloud API            │
  │ └─ GitHub API               │
  └───┬───────┬───────┬───┬────┘
      │       │       │   │
   ┌──▼┐  ┌──▼┐  ┌───▼┐ │
   │Fab│  │PBI│  │DBT │ │
   └───┘  └───┘  └────┘ │
                   GitHub
```

## 📖 Documentation Structure

```
docs/
├── getting-started/
│   ├── installation.md
│   ├── quick-start.md
│   └── architecture.md
├── workloads/
│   ├── fabric/
│   ├── powerbi/
│   ├── dataops/
│   ├── dbt/
│   └── github-actions/
├── tutorials/
│   ├── end-to-end-pipeline.md
│   ├── multi-environment-setup.md
│   └── quality-framework.md
├── advanced/
│   ├── performance-tuning.md
│   ├── security-compliance.md
│   └── cost-optimization.md
└── troubleshooting/
    └── common-issues.md
```

## 🎓 Learning Paths

### Data Engineer Path
1. Fabric Provisioning → Lakehouse Config
2. DBT Modeling → Testing & Freshness
3. Data Ops → Quality Expectations
4. GitHub Actions → CI/CD Automation

### Analytics Engineer Path
1. Power BI Semantic Modeling
2. DAX Fundamentals → Optimization
3. Report Design & Interactivity
4. Fabric + PowerBI Integration

### DevOps/Platform Engineer Path
1. Fabric Workspace Operations
2. GitHub Actions Multi-Environment
3. Monitoring & Cost Optimization
4. Security & Governance

## 🔧 Configuration Files

```
config/
├── fabric-workspaces-spec.json
├── powerbi-models-spec.json
├── dbt-project-config.yml
├── github-workflow-spec.json
└── data-contracts.yaml
```

## 📊 Example Projects

```
examples/
├── ecommerce-analytics/        # Complete E2E pipeline
├── financial-reporting/        # Multi-env with governance
├── real-time-monitoring/       # Streaming + batch
└── enterprise-dw/              # DW migration
```

## 🔐 Security

- GitHub Secrets for CI/CD
- Azure Key Vault for production
- Service Principal authentication
- Never commit `.env` or secrets
- Use `.env.example` as template

## 🚀 Installation

```bash
# Clone
git clone <repo-url>
cd AzureFabricMCP

# Setup
pip install -r requirements.txt
npm install
cp .env.example .env

# Configure
# Edit .env with your credentials
source .env

# Verify
python scripts/verify-setup.py
```

## 📝 Skills by Category

### Fabric (15 skills)
✅ Provisioning, Workspace Ops, Lakehouse Config, Notebooks, Ingestion, SQL Analytics, Shortcuts, Capacity, Security, Diagnostics, Monitoring, Migration, Git Integration, Analytics, Cost Optimization

### Power BI (12 skills)
✅ Semantic Modeling, DAX, Report Design, Deployment, Refresh, Performance, RLS, Dataflows, Paginated Reports, Mobile, Embedded, PowerQuery

### Data Ops (10 skills)
✅ Quality Expectations, SLA Monitoring, Data Contracts, Anomaly Detection, Lineage, Audit Logging, Incident Response, Profiling, Reconciliation, Cost Analysis

### DBT (10 skills)
✅ Fundamentals, Source Freshness, Testing, Docs, Slim CI, Seeds, Snapshots, Cloud Orchestration, Fabric Integration, Performance

### GitHub Actions (8 skills)
✅ CI/CD Setup, Fabric Deployment, Power BI Deployment, Multi-Environment, Secrets, Testing, Gates, Notifications

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

MIT License - See [LICENSE](LICENSE)

## 📞 Contact

- **GitHub:** [Issues & Discussions](https://github.com/techtonic/azurefabricmcp)
- **Email:** support@techtonic.ai
- **Docs:** https://techtonic.ai/docs

---

**Status:** ✅ Production Ready  
**Version:** 1.0  
**Last Updated:** July 20, 2026
