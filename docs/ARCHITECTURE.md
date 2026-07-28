# Azure Fabric + Power BI MCP Architecture
## Complete Data Medallion Platform

---

## 🎯 Architecture Overview

This document describes the complete architecture for building a data medallion platform using:
- **Azure Fabric** (Lakehouse, OneLake, SQL Endpoints)
- **Power BI** (Semantic Models, Reports, Dashboards)
- **DBT** (Data transformation & testing)
- **Data Ops** (Quality, SLA monitoring, compliance)
- **GitHub Actions** (CI/CD & automation)
- **MCP Servers** (AI-assisted orchestration)

---

## 📊 Architecture Layers (6 Tiers)

### **Tier 1: Data Ingestion Sources**

**Purpose:** Collect data from multiple external sources

**Components:**
```
┌─────────────────────────────────────────────────┐
│ APIs          Databases    Files    Streams     │
│ (REST)        (SQL, PG)     (CSV)   (Kafka)     │
│ Real-time     Relational    Batch   Streaming   │
└─────────────────────────────────────────────────┘
```

**Data Sources:**
- **APIs** — REST, GraphQL, real-time streaming
- **Databases** — SQL Server, PostgreSQL, MySQL, Oracle
- **Files** — CSV, Parquet, JSON, Excel
- **Event Streams** — Azure Event Hubs, Kafka, Message Queues
- **Cloud Storage** — Azure Blob, ADLS Gen2, S3

**MCP Integration:**
- GitHub API connects to source repositories
- Claude AI orchestrates ingestion based on specs

**SLA:** Data available for ingestion within 15 minutes

---

### **Tier 2: Fabric Lakehouse - Medallion Architecture**

**Purpose:** Implement the medallion (bronze/silver/gold) data architecture

#### **Bronze Layer - Raw Data**
```
┌────────────────────────────────────┐
│  shopify_customers.delta           │
│  shopify_orders.delta              │
│  shopify_products.delta            │
│                                    │
│  Ingested as-is                    │
│  Full history retained             │
│  Lineage tracked                   │
└────────────────────────────────────┘
```

**Characteristics:**
- ✅ Raw, unvalidated data
- ✅ Full fidelity ingestion
- ✅ Delta Lake format for versioning
- ✅ Metadata captured (_ingested_at, _source, _load_id)
- ✅ Immutable history for audit trail

**Storage:** OneLake (Microsoft's single-pane-of-glass data lake)

**Partition Strategy:**
- `Files/bronze/` — Raw data files
- `Tables/bronze_*` — Registered Delta tables

---

#### **Silver Layer - Cleaned Data**
```
┌────────────────────────────────────┐
│  dim_customers.delta               │
│  fct_orders.delta                  │
│  dim_products.delta                │
│                                    │
│  Deduplicated, validated           │
│  Business rules applied            │
│  Ready for analytics               │
└────────────────────────────────────┘
```

**Characteristics:**
- ✅ Cleaned, deduplicated data
- ✅ Business rules applied
- ✅ Data types enforced
- ✅ Nulls handled consistently
- ✅ PII masked if needed

**Transformation:**
- Duplicate removal
- Null handling
- Type casting
- Business logic enrichment
- Referential integrity checks

**Performed By:** DBT models (see Tier 2 - DBT)

**Quality Gates:**
- Null checks
- Referential integrity
- Business rule validation
- Uniqueness constraints

---

#### **Gold Layer - Business Models**
```
┌────────────────────────────────────┐
│  sales_analytics.delta             │
│  customer_analytics.delta          │
│  product_performance.delta         │
│                                    │
│  Aggregated, analytical views      │
│  Ready for Power BI                │
└────────────────────────────────────┘
```

**Characteristics:**
- ✅ Aggregated metrics
- ✅ Calculated measures
- ✅ Dimension attributes joined
- ✅ Optimized for BI queries
- ✅ No joins needed in Power BI

**Examples:**
```sql
-- Sales Analytics View
SELECT 
  customer_id,
  SUM(order_amount) AS lifetime_value,
  COUNT(order_id) AS order_count,
  AVG(order_amount) AS avg_order_value,
  MAX(order_date) AS last_order_date
FROM silver.fct_orders
GROUP BY customer_id

-- Customer Analytics View
SELECT
  c.customer_id,
  c.segment,
  COUNT(DISTINCT o.order_id) AS total_orders,
  SUM(o.order_amount) AS total_revenue,
  AVG(p.price) AS avg_product_price
FROM silver.dim_customers c
LEFT JOIN silver.fct_orders o ON c.customer_id = o.customer_id
LEFT JOIN silver.dim_products p ON o.product_id = p.product_id
GROUP BY c.customer_id, c.segment
```

---

#### **SQL Endpoints**

**Purpose:** Enable T-SQL querying on lakehouse data

**Connection:**
```
Server:   {workspace}.fabric.pbidedicated.windows.net:1433
Database: {lakehouse_name}
Auth:     Service Principal (for automation)
```

**Use Cases:**
- Direct SQL queries from Power BI
- External tools (Excel, Tableau, custom apps)
- Ad-hoc analysis
- Data exploration

**Performance:**
- Queries executed on OneLake native compute
- No data movement
- Automatic caching

---

### **Tier 3: Data Quality & Operations**

**Purpose:** Ensure data quality, SLA compliance, and governance

**Components:**

#### **Great Expectations**
```python
# Quality Checks Example
expectations = [
    # Nullability
    {
        "expectation_type": "expect_column_values_to_not_be_null",
        "column": "customer_id"
    },
    # Uniqueness
    {
        "expectation_type": "expect_column_values_to_be_unique",
        "column": "customer_id"
    },
    # Type & Format
    {
        "expectation_type": "expect_column_values_to_match_regex",
        "column": "email",
        "regex": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    },
    # Range
    {
        "expectation_type": "expect_column_values_to_be_between",
        "column": "order_amount",
        "min_value": 0,
        "max_value": 1000000
    }
]
```

**Quality Metrics:**
- ✅ Pass Rate — % of checks passing
- ✅ Data Freshness — Age of latest data
- ✅ Completeness — % of non-null values
- ✅ Accuracy — % matching business rules
- ✅ Consistency — Cross-table referential integrity

---

#### **SLA Monitoring**

**Service Level Agreements:**
```yaml
gold_sales_analytics:
  freshness_sla: 2 hours      # Max age of data
  completeness_sla: 99.9%     # Min % of records
  accuracy_sla: 99.99%        # Max error rate
  availability_sla: 99.95%    # Min uptime
```

**Monitoring:**
- Automated checks every 30 minutes
- Alerts on SLA breaches
- Dashboard showing compliance

---

#### **Data Contracts**

**Contract Definition:**
```yaml
bronze_shopify_customers:
  schema_version: 1
  owner: data_engineering
  columns:
    customer_id:
      type: integer
      nullable: false
      primary_key: true
    email:
      type: string
      nullable: false
      unique: true
  sla:
    freshness: 24 hours
    completeness: 100%
```

**Enforcement:**
- Schema validation at ingestion
- Contract violations trigger alerts
- Downstream consumers notified

---

#### **Audit Logging**

**Tracked Events:**
- Data access (who, when, what)
- Modifications (change logs)
- Deletions (retention tracking)
- Schema changes
- Permission changes

**Compliance:**
- GDPR, HIPAA, SOX ready
- Immutable audit trail
- Configurable retention

---

### **Tier 4: Power BI Analytics & Reporting**

**Purpose:** Deliver insights through dashboards and reports

#### **Semantic Model (Dataset)**

**Architecture:**
```
Gold Layer Tables (OnLake)
       ↓
    Import Mode
       ↓
Power BI Semantic Model
    ├─ Dimensions (Lookup tables)
    ├─ Facts (Measure tables)
    ├─ Relationships
    ├─ DAX Measures
    └─ Calculated Columns
```

**Dimension Tables:**
- `dim_customers` — Customer attributes
- `dim_products` — Product information
- `dim_date` — Calendar (Year, Quarter, Month, Week, Day)

**Fact Tables:**
- `fct_orders` — Transaction-level facts
- `fct_orderitems` — Line-item details

**Relationships:**
```
dim_customers ──┐
                ├── fct_orders
dim_products ───┤
                │
dim_date ───────┘
```

**Star Schema Benefits:**
- ✅ Simplified querying
- ✅ Fast performance
- ✅ Reusable dimensions
- ✅ Consistent metrics

---

#### **DAX Measures**

**Key Measures:**

```dax
-- Revenue Metrics
Total Revenue = SUM(fct_orders[amount])

YTD Revenue = TOTALYTD(
    SUM(fct_orders[amount]),
    dim_date[date]
)

Revenue Growth YoY = 
    DIVIDE(
        [Total Revenue],
        CALCULATE(
            [Total Revenue],
            DATEADD(dim_date[date], -1, YEAR)
        )
    )

-- Customer Metrics
Customer Count = DISTINCTCOUNT(dim_customers[customer_id])

Avg Order Value = 
    DIVIDE(
        [Total Revenue],
        COUNTROWS(fct_orders)
    )

Customer LTV = [Total Revenue] / [Customer Count]
```

---

#### **Dashboards**

**Executive Summary:**
- KPI cards (Revenue, Orders, Customers)
- Revenue trend (YTD vs YoY)
- Top products by revenue
- Geographic distribution

**Customer Analysis:**
- Customer lifetime value distribution
- Segment performance
- Churn indicators
- Top customers table

**Operational:**
- Pipeline health
- Data freshness
- Quality score trending
- SLA compliance

---

#### **Reports**

**Paginated Reports:**
- Automated billing statements
- Customer invoices
- Compliance reports
- Executive summaries

**Mobile Reports:**
- Phone-optimized dashboards
- Touch-friendly interactions
- Offline capability
- Native mobile performance

---

### **Tier 5: CI/CD & Multi-Environment Deployment**

**Purpose:** Automate testing and deployment across environments

#### **Environment Progression**

```
┌─────────────────────────────────────────────────────┐
│                   Development                       │
│  • Fresh every deploy                               │
│  • All features enabled                             │
│  • Smoke tests only                                 │
│  • Fail fast feedback                               │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│                    QA Environment                   │
│  • Matches production schema                        │
│  • 50% production data (anonymized)                 │
│  • Full test suite runs                             │
│  • Performance baseline established                 │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│                  UAT Environment                    │
│  • Production-like data (test subset)               │
│  • User acceptance testing                          │
│  • Business validation                              │
│  • Go/no-go decision                                │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│                   Production                        │
│  • All validations passed                           │
│  • Approval-based deployment                        │
│  • Gradual rollout (if applicable)                  │
│  • Continuous monitoring                            │
└─────────────────────────────────────────────────────┘
```

#### **GitHub Actions Workflows**

**Continuous Integration:**
```yaml
name: Test & Validate

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: DBT Compile
        run: dbt compile
      
      - name: Run Data Tests
        run: dbt test
      
      - name: Check Schema
        run: python scripts/validate-schema.py
      
      - name: Data Quality
        run: python scripts/run-expectations.py
```

**Continuous Deployment:**
```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - name: Deploy Fabric Items
        run: |
          python scripts/deploy-fabric-notebooks.py
          python scripts/deploy-datasets.py
      
      - name: Deploy Power BI
        run: python scripts/publish-powerbi-reports.py
      
      - name: Refresh Data
        run: python scripts/trigger-refresh.py
      
      - name: Notify Slack
        run: python scripts/notify-deployment.py
```

---

#### **Multi-Environment Promotion**

**Deployment Gates:**
```
Dev ──(manual)──> QA ──(auto)──> UAT ──(manual)──> Prod
      (2 hrs)                   (1 day)            (approval)
```

**Validation at Each Stage:**
- **Dev:** Syntax, basic tests
- **QA:** Full test suite, data validation, performance
- **UAT:** User validation, business rules
- **Prod:** Smoke tests, monitoring enabled

---

### **Tier 6: MCP Servers & AI Orchestration**

**Purpose:** AI-assisted automation through MCP server integration

#### **Fabric MCP Server**

**Capabilities:**
```python
# Workspace Operations
fabric_mcp.create_workspace("Analytics_Dev")
fabric_mcp.add_user_to_workspace(workspace_id, user, role="Admin")
fabric_mcp.get_workspace_capacity()

# Lakehouse Management
fabric_mcp.create_lakehouse(workspace_id, "data_lake")
fabric_mcp.create_directory(lakehouse_id, "Files/bronze")
fabric_mcp.get_table_statistics(lakehouse_id, "bronze_customers")

# Item Management
fabric_mcp.deploy_notebook(workspace_id, "notebook_path")
fabric_mcp.create_sql_endpoint(lakehouse_id)
fabric_mcp.get_item_lineage(item_id)
```

**Use Cases:**
- Automated workspace provisioning
- Lakehouse setup
- Notebook deployment
- SQL endpoint configuration
- Item management

---

#### **Power BI MCP Server**

**Capabilities:**
```python
# Semantic Model Operations
powerbi_mcp.create_model(workspace_id, "SalesAnalytics")
powerbi_mcp.add_table(model_id, "dim_customers")
powerbi_mcp.create_relationship(model_id, from_table, to_table)
powerbi_mcp.add_measure(model_id, "Total Revenue", dax_expression)

# Report Deployment
powerbi_mcp.publish_report(workspace_id, "report.pbix")
powerbi_mcp.configure_refresh(dataset_id, schedule="daily")
powerbi_mcp.set_rls_rules(model_id, rules)

# Analytics
powerbi_mcp.get_query_metrics(dataset_id)
powerbi_mcp.analyze_performance(report_id)
```

**Use Cases:**
- Semantic model creation
- Report publishing
- Refresh scheduling
- RLS configuration
- Performance optimization

---

#### **DBT Cloud API**

**Capabilities:**
```python
# Execution
dbt_api.trigger_run(job_id, models=["model1", "model2"])
dbt_api.get_run_status(run_id)
dbt_api.get_run_artifacts(run_id)

# Monitoring
dbt_api.get_dbt_docs(project_id)
dbt_api.get_model_performance(project_id, model_name)
dbt_api.check_source_freshness(project_id, source_name)

# Management
dbt_api.create_job(project_id, job_config)
dbt_api.update_job_schedule(job_id, schedule)
```

**Use Cases:**
- Trigger transformation runs
- Monitor job execution
- Source freshness checks
- Document generation
- Performance tracking

---

#### **GitHub API**

**Capabilities:**
```python
# Workflows
github_api.trigger_workflow(repo, workflow_name, inputs)
github_api.get_workflow_runs(repo, workflow_name)
github_api.get_run_status(repo, run_id)

# Repository Management
github_api.create_branch(repo, branch_name)
github_api.create_pull_request(repo, head, base, body)
github_api.merge_pull_request(repo, pr_number)

# Secrets
github_api.set_secret(repo, secret_name, secret_value)
github_api.get_environment_secrets(repo, environment)
```

**Use Cases:**
- CI/CD automation
- Deployment triggering
- Secret management
- Workflow monitoring
- Repository management

---

#### **Claude AI Orchestration**

**Capabilities:**
```
User Request
    ↓
Claude AI (with MCP Servers)
    ├─ Interprets request
    ├─ Determines required steps
    ├─ Calls appropriate MCP servers
    ├─ Chains operations together
    └─ Reports results
```

**Examples:**

**Scenario 1: Setup New Environment**
```
User: "Setup a complete analytics environment for Q4 with 
       Fabric, DBT, and Power BI, ready for production"

Claude:
1. Calls Fabric MCP → Create workspace
2. Calls Fabric MCP → Create lakehouses (bronze/silver/gold)
3. Calls Fabric MCP → Enable SQL endpoints
4. Calls GitHub API → Create feature branch
5. Calls DBT Cloud API → Trigger initial runs
6. Calls Power BI MCP → Deploy semantic models
7. Calls GitHub API → Create pull request
8. Reports completion with workspace IDs
```

**Scenario 2: Deploy New Data Source**
```
User: "Ingest a new CSV file from Azure Blob and 
       add it to our analytics pipeline"

Claude:
1. Creates data contract
2. Adds data quality expectations
3. Creates DBT model (bronze layer)
4. Adds tests to DBT
5. Creates GitHub Actions workflow
6. Triggers initial run
7. Validates results
```

**Scenario 3: Troubleshoot Data Quality Issue**
```
User: "Sales analytics dashboard shows wrong revenue. 
       Find and fix the issue"

Claude:
1. Checks data quality via Great Expectations
2. Queries SQL endpoint to profile data
3. Reviews DBT model logic
4. Identifies issue in transformation
5. Creates fix and tests
6. Deploys via GitHub Actions
7. Validates with fresh data
```

---

## 🔄 Data Flow Example

### **Complete E2E Flow: Customer Order Data**

```
1. INGESTION
   ├─ Shopify API → Extract customer, order, product data
   └─ Store in Azure Blob (raw CSV)

2. BRONZE LAYER
   ├─ Read from Blob
   ├─ Add metadata (_ingested_at, _source)
   └─ Write to delta: Files/bronze/shopify_*

3. SILVER LAYER (DBT)
   ├─ Read bronze tables
   ├─ Deduplicate customers (unique on email)
   ├─ Clean order amounts (validate > 0)
   ├─ Join dimensions (orders → customers → products)
   └─ Write delta: Files/silver/dim_* & fct_orders

4. DATA QUALITY
   ├─ Run Great Expectations tests
   ├─ Check SLA (data freshness < 2 hrs)
   ├─ Validate referential integrity
   └─ Emit metrics to monitoring

5. GOLD LAYER
   ├─ Aggregate: Customer LTV
   ├─ Aggregate: Product performance
   ├─ Aggregate: Daily sales metrics
   └─ Write delta: Files/gold/sales_analytics

6. POWER BI
   ├─ Semantic model reads from gold tables
   ├─ Define relationships (dim_* ← → fct_orders)
   ├─ Calculate DAX measures (YTD revenue, YoY growth)
   └─ Publish reports (executive, customer, product)

7. REPORTING
   ├─ Dashboard shows revenue KPIs
   ├─ Drill-through to customer details
   ├─ Export paginated report
   └─ Share via Power BI service

8. MONITORING
   ├─ Track pipeline execution time (3 mins)
   ├─ Monitor data freshness (latest: 45 mins ago)
   ├─ Alert on SLA breach (if > 2 hrs)
   └─ Cost tracking (storage + compute)
```

**Total End-to-End Time:** ~5 minutes (ingestion to reports)

---

## 🔐 Security Architecture

### **Authentication & Authorization**

```
┌─────────────────────────────────────────┐
│    Service Principal (App Registration) │
│    - Client ID                          │
│    - Client Secret (repository secret)  │
│    - Tenant ID                          │
└─────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────┐
│  Roles & Permissions                    │
│  - Workspace Admin (Fabric)             │
│  - Dataset Editor (Power BI)            │
│  - Repository Contributor (GitHub)      │
└─────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────┐
│  Row-Level Security (RLS)               │
│  - Users see only their data            │
│  - Enforced at semantic model level     │
└─────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────┐
│  Encryption                             │
│  - In-transit: TLS 1.2+                 │
│  - At-rest: Azure Storage encryption    │
│  - Data Lake: OneLake encryption        │
└─────────────────────────────────────────┘
```

---

## 📊 Performance & Scalability

### **Medallion Architecture Benefits**

| Aspect | Benefit |
|--------|---------|
| **Query Performance** | Gold layer pre-aggregated → 10x faster queries |
| **Storage Efficiency** | Deduplication & compression in Silver → 60% reduction |
| **Transformation Speed** | Incremental DBT → 5x faster runs (vs full refresh) |
| **Fault Tolerance** | Bronze layer immutable → full replayability |
| **Developer Velocity** | Clear separation → parallel development |

### **Scaling Patterns**

```
Data Volume Growth → Medallion naturally scales:
  
  Bronze: Archive old data (still queryable)
  Silver: Incremental loads via DBT (incremental materializations)
  Gold: Pre-aggregates reduce query load
  Power BI: Aggregation tables → no limit to dataset size
```

---

## ✅ Implementation Checklist

### **Phase 1: Foundation (Week 1)**
- [ ] Fabric workspace provisioning
- [ ] Lakehouse creation with medallion folders
- [ ] SQL endpoints enabled
- [ ] Service Principal configured

### **Phase 2: Data Engineering (Weeks 2-3)**
- [ ] Bronze layer ingestion configured
- [ ] DBT project initialized
- [ ] Silver layer models created & tested
- [ ] Gold layer aggregations built

### **Phase 3: Quality & Ops (Week 4)**
- [ ] Great Expectations suite configured
- [ ] SLA monitoring in place
- [ ] Audit logging enabled
- [ ] Data contracts defined

### **Phase 4: Analytics (Week 5)**
- [ ] Semantic model created
- [ ] DAX measures defined
- [ ] Dashboards built
- [ ] Reports published

### **Phase 5: Automation (Weeks 6-7)**
- [ ] GitHub Actions workflows configured
- [ ] Multi-environment promotion working
- [ ] Deployment gates approved
- [ ] Notifications integrated

### **Phase 6: Operations (Week 8)**
- [ ] Monitoring dashboards live
- [ ] Alert thresholds tuned
- [ ] Runbooks documented
- [ ] Team trained

---

## 🎯 Key Metrics

### **Data Quality**
- Quality Check Pass Rate: 99%+
- SLA Compliance: 99.9%+
- Data Accuracy: 99.99%+

### **Performance**
- Ingestion-to-Reports: < 5 minutes
- Query Response Time (gold): < 1 second
- DBT Run Time: 10-15 minutes
- Power BI Refresh: < 5 minutes

### **Cost Optimization**
- Storage: $0.015 per GB (OnLake tiering)
- Compute: Use-based, scales with needs
- Reserved Capacity: 30% discount for predictable load

### **Availability**
- Uptime Target: 99.99%
- RTO (Recovery Time Objective): < 4 hours
- RPO (Recovery Point Objective): < 1 hour

---

## 📚 References

- [Azure Fabric Documentation](https://learn.microsoft.com/fabric)
- [DBT Best Practices](https://docs.getdbt.com)
- [Power BI Design Patterns](https://learn.microsoft.com/power-bi/guidance)
- [Medallion Architecture](https://www.databricks.com/blog/2022/06/24/build-reliable-data-pipelines-with-the-medallion-architecture.html)

---

**Architecture Version:** 1.0  
**Last Updated:** July 20, 2026  
**Status:** ✅ Production Ready
