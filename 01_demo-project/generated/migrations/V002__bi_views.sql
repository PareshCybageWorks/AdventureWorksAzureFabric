-- =============================================================================
-- V002__bi_views.sql
-- =============================================================================
-- GENERATED from the gold mapping by framework/generators/generate_ddl.py.
-- Do not edit by hand: change the spec and regenerate, or the next run
-- overwrites this file and the drift is silent.
--
-- Idempotent. CREATE OR ALTER means re-running converges rather than failing,
-- so a migration can be applied to an environment in any state.
-- =============================================================================

-- Views over the physical star schema.
--
-- Power BI binds HERE, never to the physical tables. That
-- indirection is the point: a report can be reshaped by altering
-- a view instead of migrating a table other things depend on.

-- vw_sales_summary
--   Daily sales rollup, the default starting point for trend reporting
--   Grain: One row per day per region
CREATE OR ALTER VIEW [bi].[vw_sales_summary] AS
SELECT
    d.full_date,
    d.year,
    d.quarter,
    d.month_name,
    d.fiscal_year,
    c.region,
    COUNT(DISTINCT f.order_id)  AS orders,
    SUM(f.quantity)             AS units_sold,
    SUM(f.recognised_revenue)   AS revenue,
    SUM(f.gross_margin)         AS gross_margin,
    CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
         ELSE SUM(f.recognised_revenue) / COUNT(DISTINCT f.order_id)
    END                         AS avg_order_value
FROM dbo.fct_sales      f
JOIN dbo.dim_date       d ON f.order_date_sk = d.date_sk
JOIN dbo.dim_customer   c ON f.customer_sk   = c.customer_sk
GROUP BY d.full_date, d.year, d.quarter, d.month_name, d.fiscal_year, c.region;

GO

-- vw_customer_360
--   Customer-level lifetime metrics and value segmentation
--   Grain: One row per customer
CREATE OR ALTER VIEW [bi].[vw_customer_360] AS
WITH customer_totals AS (
    SELECT
        c.customer_sk,
        c.customer_id,
        c.full_name,
        c.region,
        c.tenure_band,
        COUNT(DISTINCT f.order_id) AS total_orders,
        SUM(f.recognised_revenue)  AS lifetime_value,
        MIN(d.full_date)           AS first_order_date,
        MAX(d.full_date)           AS last_order_date
    FROM dbo.dim_customer c
    JOIN dbo.fct_sales    f ON f.customer_sk   = c.customer_sk
    JOIN dbo.dim_date     d ON f.order_date_sk = d.date_sk
    WHERE c.is_current = 1
    GROUP BY c.customer_sk, c.customer_id, c.full_name, c.region, c.tenure_band
)
SELECT
    *,
    CASE WHEN total_orders = 0 THEN 0
         ELSE lifetime_value / total_orders END AS avg_order_value,
    DATEDIFF(day, last_order_date, CAST(GETDATE() AS DATE)) AS days_since_last_order,
    -- Value tiers are quantile-based rather than fixed thresholds, so
    -- they stay meaningful as absolute revenue grows.
    -- NTILE rather than PERCENTILE_CONT ... OVER (), which the Fabric
    -- Warehouse does not reliably support. Same four bands, plain window
    -- syntax, and it stays quantile-based so the tiers remain meaningful
    -- as absolute revenue grows.
    CASE NTILE(10) OVER (ORDER BY lifetime_value)
        WHEN 10 THEN 'Platinum'
        WHEN 9  THEN 'Gold'
        WHEN 8  THEN 'Gold'
        WHEN 7  THEN 'Silver'
        WHEN 6  THEN 'Silver'
        WHEN 5  THEN 'Silver'
        ELSE 'Bronze'
    END AS value_tier,
    CASE
        WHEN DATEDIFF(day, last_order_date, CAST(GETDATE() AS DATE)) > 180 THEN 'Churn risk'
        WHEN DATEDIFF(day, last_order_date, CAST(GETDATE() AS DATE)) >  90 THEN 'Cooling'
        ELSE 'Active'
    END AS engagement_status
FROM customer_totals;

GO

-- vw_product_performance
--   Product-level sales and margin
--   Grain: One row per product
CREATE OR ALTER VIEW [bi].[vw_product_performance] AS
SELECT
    p.product_sk,
    p.product_id,
    p.product_name,
    p.category,
    p.price_band,
    p.stock_status,
    COUNT(DISTINCT f.order_id) AS orders,
    SUM(f.quantity)            AS units_sold,
    SUM(f.recognised_revenue)  AS revenue,
    SUM(f.gross_margin)        AS gross_margin,
    CASE WHEN SUM(f.recognised_revenue) = 0 THEN 0
         ELSE SUM(f.gross_margin) / SUM(f.recognised_revenue) * 100
    END                        AS margin_pct
FROM dbo.dim_product p
JOIN dbo.fct_sales   f ON f.product_sk = p.product_sk
WHERE p.is_current = 1
GROUP BY p.product_sk, p.product_id, p.product_name, p.category,
         p.price_band, p.stock_status;

GO

-- vw_data_quality
--   Quality posture exposed as a reportable view. Makes the pipeline's own health visible in the same dashboard as the business numbers, rather than hidden in a log only the platform team reads.
--   Grain: One row per table per load
CREATE OR ALTER VIEW [bi].[vw_data_quality] AS
SELECT
    load_id,
    table_name,
    layer,
    rows_in,
    rows_out,
    rows_quarantined,
    rows_corrected,
    CASE WHEN rows_in = 0 THEN 0
         ELSE CAST(rows_out AS FLOAT) / rows_in * 100
    END AS pass_rate_pct,
    checks_passed,
    checks_failed,
    processed_at
-- Cross-item read. The DQ log is written by EVERY layer, and each
-- notebook writes it unqualified, so it lands in that notebook's own
-- default lakehouse rather than here. The view consolidates them.
--
-- Consolidating at read time is deliberate: routing every layer's log to
-- the warehouse would couple bronze and silver to gold for nothing more
-- than logging.
FROM (
    SELECT * FROM [lh_bronze].[dbo].[dq_run_log]
    UNION ALL
    SELECT * FROM [lh_silver].[dbo].[dq_run_log]
) AS dq_run_log;
