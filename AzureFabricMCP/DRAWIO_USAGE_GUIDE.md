# Draw.io Diagram Usage Guide

## 📊 Azure Fabric + Power BI MCP Architecture Diagram

### Overview

You now have a **professional draw.io diagram** that visualizes the complete Azure Fabric + Power BI MCP architecture with medallion data architecture.

**File:** `Azure_Fabric_PowerBI_Architecture.drawio`

---

## 🚀 How to Open the Diagram

### Option 1: Using draw.io Web (Recommended)

1. Go to https://app.diagrams.net
2. Click **File** → **Open**
3. Select the file: `Azure_Fabric_PowerBI_Architecture.drawio`
4. Diagram loads in browser
5. Edit, add notes, export as needed

### Option 2: Using diagrams.net Desktop App

1. Download from https://github.com/jgraph/drawio-desktop/releases
2. Install on your computer
3. Open the `.drawio` file
4. Start editing

### Option 3: Using draw.io Editor (Offline)

1. Download draw.io desktop version
2. Works offline - perfect for presentations
3. No internet required

### Option 4: In VS Code

1. Install "Draw.io Integration" extension
2. Right-click `.drawio` file
3. Select "Open in Draw.io"
4. Edit directly in VS Code

---

## 📐 Diagram Structure

### **6 Layers Included:**

#### **Layer 1: Data Ingestion Sources** (Blue)
- APIs (REST, GraphQL)
- Databases (SQL, PostgreSQL)
- Files (CSV, Parquet, JSON)
- Event Streams (Kafka, Event Hub)
- Cloud Storage (Azure Blob, ADLS)

#### **Layer 2: Fabric Medallion Architecture** (Brown/Silver/Gold)
- **Bronze Layer** 🔷 — Raw data ingestion
- **Silver Layer** 🔶 — Transformed, cleaned data
- **Gold Layer** 🏆 — Aggregated, BI-ready data
- **SQL Endpoints** — T-SQL query access
- **DBT Transformations** — Data transformation pipeline

#### **Layer 3: Data Quality & Operations** (Tan)
- Great Expectations quality checks
- SLA monitoring (freshness, completeness)
- Data contracts & governance
- Audit logging & compliance

#### **Layer 4: Power BI Analytics** (Orange)
- Semantic Models (star schema)
- DAX Measures (YTD, YoY, LTV)
- Dashboards (Executive, Operational)
- Reports (Paginated, Mobile)

#### **Layer 5: CI/CD Deployment** (Brown)
- GitHub Actions workflows
- Dev Environment (Testing)
- QA Environment (Validation)
- Production Environment (Live)

#### **Layer 6: MCP Servers** (Light Blue & Green)
- Fabric MCP — Workspace & Lakehouse ops
- Power BI MCP — Semantic models & deployment
- DBT Cloud API — Transformations & testing
- GitHub API — CI/CD & automation
- Claude AI — Intelligent orchestration

#### **Support Services** (Right Side)
- 📊 Monitoring (Health, Metrics, Alerts)
- 🔒 Security (Authentication, RLS/OLS)
- ✓ Compliance (Auditing, Governance)

---

## ✏️ How to Edit the Diagram

### Add New Components

1. **Insert Shape:** Right-click → Insert → Shape
2. **Add Text:** Double-click box to add text
3. **Connect Arrows:** Drag connector between boxes
4. **Change Colors:** Select box → Format Panel → Fill color

### Add Annotations

```
Right-click → Add Note/Comment
Useful for:
- Marking ownership
- Adding implementation notes
- Flagging risks/issues
- Recording decisions
```

### Export for Presentations

1. **File** → **Export**
2. Choose format:
   - **PNG** (for slides)
   - **SVG** (for websites)
   - **PDF** (for printing)
3. Adjust scale (300% for high-quality)

### Share with Team

1. **File** → **Share**
2. Generate link
3. Send to stakeholders
4. Enable/disable editing

---

## 🎨 Color Scheme

| Layer | Color | Meaning |
|-------|-------|---------|
| **Ingestion** | Light Blue | Data sources |
| **Bronze** | Brown (#A0826D) | Raw data |
| **Silver** | Gray (#C0C0C0) | Transformed data |
| **Gold** | Gold (#FFD700) | Analytics-ready |
| **Quality** | Tan (#D4A574) | Data validation |
| **Power BI** | Orange (#FF8C00) | Analytics |
| **CI/CD** | Brown (#8B6F47) | Deployment |
| **MCP** | Light Blue (#4B90E2) | Orchestration |
| **Claude** | Green (#228B22) | AI automation |

---

## 📍 Key Elements to Know

### Boxes (Components)
```
┌─────────────────┐
│   Component     │  Each box represents a system/service
│   Description   │
└─────────────────┘
```

### Arrows (Data Flow)
```
→ Synchronous    (Solid arrow) = Real-time flow
⇢ Asynchronous   (Dashed) = Event-driven/delayed
```

### Layers
```
Tier 1 ──────────────
   ↓
Tier 2 ──────────────
   ↓
Tier 3 ──────────────
   ↓
... (continuous flow down to Tier 6)
```

---

## 🔄 Example Workflows in Diagram

### Workflow 1: Data Ingestion to Reports (5 minutes)

```
API Source
    ↓
Bronze Layer (Raw data stored)
    ↓
DBT Transform
    ↓
Silver Layer (Cleaned data)
    ↓
Quality Checks (Validations pass)
    ↓
Gold Layer (Aggregates)
    ↓
Power BI Semantic Model
    ↓
Dashboard Published
    ↓
Monitoring Dashboard (Real-time)
```

### Workflow 2: Code to Production (CI/CD)

```
GitHub Push (main branch)
    ↓
GitHub Actions (Workflow triggered)
    ↓
Tests Run (DBT tests, quality checks)
    ↓
Dev Deploy (Fresh environment)
    ↓
QA Deploy (50% production data)
    ↓
Approval Gate (Manual validation)
    ↓
Production Deploy (Live to end users)
    ↓
Monitoring (Health checks, SLA tracking)
```

---

## 📋 Use Cases for the Diagram

### Use Case 1: Architecture Review
```
Print the diagram → Pin on wall → Discuss with stakeholders
Useful for identifying:
- Missing components
- Security gaps
- Performance bottlenecks
- Compliance issues
```

### Use Case 2: Onboarding New Team Members
```
Share diagram → Explain each layer → Walk through data flow
Helps new team members understand:
- How data flows through system
- Team responsibilities
- Integration points
- Deployment process
```

### Use Case 3: Design New Data Source
```
Add data source to Tier 1 → Trace through medallion layers
Helps determine:
- Which ingestion pattern to use
- Required quality checks
- Transformation complexity
- Estimated implementation time
```

### Use Case 4: Troubleshoot Data Issues
```
Start at layer with issue → Follow arrows upstream/downstream
Helps identify:
- Root cause of bad data
- Which transformations failed
- Quality check that should have caught it
- How to prevent recurrence
```

### Use Case 5: Present to Stakeholders
```
Export as high-res PNG/PDF → Add your branding → Present
Shows:
- Comprehensive system design
- Professional architecture
- All integration points
- Clear data flow
```

---

## 🔧 Customization Tips

### Add Your Organization Logo
1. **Insert** → **Image**
2. Upload company logo
3. Position in top-right
4. Adjust opacity if needed

### Add Implementation Timeline
1. **Insert shapes** alongside Tier 5 (CI/CD)
2. Add week labels (Week 1-8)
3. Color-code completion status
4. Share with team

### Add Risk/Issue Markers
1. Use **red X** or **warning icons** for risks
2. Add notes explaining each risk
3. Track mitigations in diagram
4. Share with risk management team

### Add Cost Annotations
1. Add **cost labels** to each component
2. Calculate total monthly cost
3. Show cost savings vs. legacy system
4. Update quarterly as usage changes

### Create Multiple Views
1. **File** → **Save As**
2. Create variants:
   - `Architecture_Current.drawio` (today's state)
   - `Architecture_Q4_Plan.drawio` (future state)
   - `Architecture_Simplified.drawio` (executive summary)

---

## 📱 Mobile & Export Options

### Export Formats Available

**Image Files:**
- PNG (Perfect for presentations)
- JPEG (Email-friendly)
- SVG (Scalable for web)
- PDF (Printable)

**Office Documents:**
- Embed in PowerPoint slide
- Attach to Word document
- Share in Teams/Slack

**Web Sharing:**
- Generate public link
- Embed in wiki/documentation
- Share read-only or editable

---

## ✨ Pro Tips

### Tip 1: Layer Visibility
```
Use View → Show/Hide layers to focus on specific tiers
Great for explaining one layer at a time
```

### Tip 2: Add Swimlanes
```
Insert vertical lines to show team responsibilities:
- Data Engineering (Left)
- Analytics Engineering (Middle)
- DevOps/Platform (Right)
```

### Tip 3: Add Metrics Box
```
Insert text box with:
- Data volume (GB/TB)
- Query latency (ms)
- Refresh frequency (minutes)
- Cost per month ($)
```

### Tip 4: Version Control
```
Keep diagrams in Git:
Azure_Fabric_PowerBI_Architecture.v1.drawio
Azure_Fabric_PowerBI_Architecture.v1.1.drawio
...allows tracking changes over time
```

### Tip 5: Interactive Presentation
```
Use Presentation Mode (F5 in draw.io)
Click through each layer one-by-one
Great for demos and walkthroughs
```

---

## 🎯 Next Steps

1. **Open** the diagram in draw.io
2. **Review** each layer and component
3. **Customize** with your organization details
4. **Add notes** for team discussion
5. **Export** for presentations/documentation
6. **Share** with stakeholders for feedback
7. **Update** as architecture evolves

---

## 📚 Additional Resources

- **Draw.io Help:** https://www.drawio.com/doc/
- **Keyboard Shortcuts:** https://www.drawio.com/blog/shortcut-keys
- **Templates Library:** https://www.drawio.com/templates
- **Video Tutorials:** https://www.drawio.com/blog

---

## 💡 Common Questions

### Q: Can I edit the diagram in draw.io?
**A:** Yes! Full editing capabilities in draw.io web and desktop versions.

### Q: Can I share the diagram with my team?
**A:** Yes! Use File → Share to generate a link. Can be read-only or editable.

### Q: How do I export for PowerPoint?
**A:** File → Export → PNG (recommended). Then insert into PowerPoint slide.

### Q: Can I print the diagram?
**A:** Yes! File → Print or export as PDF then print. Ensure 300% scale for clarity.

### Q: How do I add my company branding?
**A:** Insert → Image to add logo. Use color scheme matching your brand guidelines.

### Q: Can I track changes/versions?
**A:** Store in Git/GitHub. Use version naming convention in filename.

---

## 📞 Support

**Need Help?**
- draw.io documentation: https://www.drawio.com/doc/
- File format: XML-based (human-readable)
- Open source: https://github.com/jgraph/drawio

---

**Diagram Version:** 1.0  
**Created:** July 20, 2026  
**Format:** draw.io XML (.drawio)  
**Status:** ✅ Ready to Use
