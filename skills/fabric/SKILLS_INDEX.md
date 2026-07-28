# Fabric Skills Index

Complete collection of Microsoft Fabric skills for workspace provisioning, management, and operations.

## 📋 Skills Directory

### Infrastructure & Provisioning (3 skills)

1. **provisioning.md**
   - Automate workspace creation
   - Lakehouse provisioning
   - SQL endpoint setup
   - Multi-environment setup

2. **workspace-operations.md**
   - User & role management
   - Access control
   - Workspace settings
   - Capacity assignment

3. **capacity-management.md**
   - Capacity planning
   - Utilization monitoring
   - Cost tracking
   - Right-sizing

### Data Layer (4 skills)

4. **lakehouse-config.md**
   - Medallion architecture
   - Table management
   - Delta Lake optimization
   - Data access roles

5. **data-ingestion.md**
   - External data sources
   - API integration
   - Database connections
   - Batch & streaming

6. **shortcuts-management.md**
   - ADLS Gen2 shortcuts
   - S3 connections
   - Azure Blob shortcuts
   - External data reference

7. **sql-analytics.md**
   - T-SQL querying
   - SQL endpoint usage
   - Query optimization
   - Performance tuning

### Automation & Development (3 skills)

8. **notebooks-automation.md**
   - PySpark notebooks
   - SQL notebooks
   - Notebook scheduling
   - Execution management

9. **git-integration.md**
   - Version control
   - Branch management
   - Commit workflows
   - CI/CD integration

10. **api-automation.md**
    - Fabric REST APIs
    - Item management
    - Workspace automation
    - Programmatic control

### Operations & Monitoring (3 skills)

11. **monitoring.md**
    - Health checks
    - Performance metrics
    - Alerting
    - Dashboard setup

12. **diagnostics.md**
    - Performance analysis
    - Query insights
    - Troubleshooting
    - Log analysis

13. **security-config.md**
    - Row-level security (RLS)
    - Column-level security (CLS)
    - Encryption
    - Access policies

### Advanced Topics (2 skills)

14. **migration.md**
    - Data warehouse migration
    - Legacy system integration
    - Data mapping
    - Validation & testing

15. **cost-optimization.md**
    - CapEx optimization
    - Compute efficiency
    - Storage optimization
    - Reserved capacity

## 🎯 Quick Navigation

### By Use Case

**Getting Started:**
- provisioning.md → workspace-operations.md → lakehouse-config.md

**Data Pipeline:**
- data-ingestion.md → lakehouse-config.md → sql-analytics.md

**Automation:**
- notebooks-automation.md → git-integration.md → api-automation.md

**Operations:**
- monitoring.md → diagnostics.md → security-config.md

### By Role

**Platform Engineer:**
- provisioning.md
- workspace-operations.md
- capacity-management.md
- security-config.md

**Data Engineer:**
- lakehouse-config.md
- data-ingestion.md
- notebooks-automation.md
- sql-analytics.md

**DevOps/Automation:**
- git-integration.md
- api-automation.md
- monitoring.md
- cost-optimization.md

## 📊 Skills Map

```
┌─ Infrastructure
│  ├─ Provisioning
│  ├─ Workspace Ops
│  └─ Capacity Mgmt
│
├─ Data Layer
│  ├─ Lakehouse Config
│  ├─ Data Ingestion
│  ├─ Shortcuts
│  └─ SQL Analytics
│
├─ Automation
│  ├─ Notebooks
│  ├─ Git Integration
│  └─ API Automation
│
├─ Operations
│  ├─ Monitoring
│  ├─ Diagnostics
│  └─ Security
│
└─ Advanced
   ├─ Migration
   └─ Cost Optimization
```

## 🔗 Learning Sequences

### Sequence 1: Complete Platform Setup
1. provisioning.md
2. workspace-operations.md
3. lakehouse-config.md
4. security-config.md
5. monitoring.md

### Sequence 2: Data Pipeline Development
1. lakehouse-config.md
2. data-ingestion.md
3. notebooks-automation.md
4. sql-analytics.md
5. git-integration.md

### Sequence 3: Operations & Monitoring
1. monitoring.md
2. diagnostics.md
3. capacity-management.md
4. cost-optimization.md
5. security-config.md

## 📈 Skill Difficulty Levels

**Beginner:** Provisioning, Workspace Operations, Lakehouse Config  
**Intermediate:** Data Ingestion, Notebooks, Git Integration, Monitoring  
**Advanced:** Diagnostics, Migration, Cost Optimization, API Automation  
**Expert:** Security Config, Complex Ingestion Patterns, Performance Tuning  

## 🔄 Prerequisite Dependencies

```
provisioning.md
    ↓
workspace-operations.md
    ↓
lakehouse-config.md
    ├→ data-ingestion.md
    │   ↓
    └→ notebooks-automation.md
        ↓
    git-integration.md
        ↓
    api-automation.md
        ↓
    monitoring.md ← diagnostics.md ← security-config.md
        ↓
    capacity-management.md
        ↓
    cost-optimization.md
```

## 📞 Using These Skills with Claude

```
"I need to set up a Fabric workspace with lakehouses"
→ Claude loads: provisioning.md → workspace-operations.md → lakehouse-config.md

"How do I ingest data from external sources?"
→ Claude loads: data-ingestion.md → lakehouse-config.md

"Setup monitoring and alerts for my Fabric environment"
→ Claude loads: monitoring.md → diagnostics.md

"Optimize costs and performance"
→ Claude loads: cost-optimization.md → capacity-management.md → diagnostics.md
```

## ✅ Skills Status

| Skill | Status | Lines | Examples |
|-------|--------|-------|----------|
| provisioning.md | ✅ Ready | 300+ | 5+ |
| workspace-operations.md | ✅ Ready | 280+ | 6+ |
| lakehouse-config.md | ✅ Ready | 350+ | 8+ |
| capacity-management.md | ✅ Ready | 250+ | 4+ |
| data-ingestion.md | ✅ Ready | 320+ | 7+ |
| shortcuts-management.md | ✅ Ready | 280+ | 5+ |
| sql-analytics.md | ✅ Ready | 290+ | 6+ |
| notebooks-automation.md | ✅ Ready | 310+ | 6+ |
| git-integration.md | ✅ Ready | 270+ | 5+ |
| api-automation.md | ✅ Ready | 300+ | 7+ |
| monitoring.md | ✅ Ready | 320+ | 6+ |
| diagnostics.md | ✅ Ready | 300+ | 7+ |
| security-config.md | ✅ Ready | 340+ | 8+ |
| migration.md | ✅ Ready | 360+ | 5+ |
| cost-optimization.md | ✅ Ready | 280+ | 6+ |

**Total Lines of Content:** 4,360+  
**Total Code Examples:** 93  
**Total Diagrams:** 25+

## 🚀 Getting Started

1. **Start here:** `provisioning.md`
2. **Then:** `workspace-operations.md` or `lakehouse-config.md`
3. **For data:** `data-ingestion.md` → `notebooks-automation.md`
4. **For ops:** `monitoring.md` → `diagnostics.md`

Each skill is **self-contained** but **cross-referenced** for easy navigation.

---

**Last Updated:** July 20, 2026  
**Total Skills:** 15  
**Status:** ✅ Production Ready
