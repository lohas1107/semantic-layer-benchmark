-- PostgreSQL translations of text_to_sql_benchmark.json expected_sql.
-- Source of truth for text_to_sql_benchmark.pg.json (scripts/pg/build_fixtures.py).
-- Each query starts with a "-- @q<N>" marker; "-- @note" lines explain translation choices.
-- "-- @ties i,j" = ORDER BY ... LIMIT over tied keys: only those 0-based columns are compared.
-- "-- @deviation text" = known, explained difference from the paper's ClickHouse answer.
--
-- Translation rules (ClickHouse -> PostgreSQL), applied to keep results identical:
--   count()                 -> count(*)
--   toYear/toMonth/toQuarter-> EXTRACT(...)::int
--   countDistinct(x)        -> count(DISTINCT x)
--   sumIf(x,c)              -> COALESCE(sum(x) FILTER (WHERE c), 0)   (sumIf returns 0, not NULL)
--   countIf/avgIf/countDistinctIf -> aggregate FILTER (WHERE c)
--   anyIf(x,c)              -> max(x) FILTER (WHERE c)   (single row per group here)
--   argMax(a,b)             -> (array_agg(a ORDER BY b DESC))[1]
--   lag/lagInFrame(x)       -> lag(x, 1, 0)   (ClickHouse returns the type default 0)
--   dateDiff('month',a,b)   -> (year(b)*12+month(b)) - (year(a)*12+month(a))
--   dateDiff('day',a,b)     -> b - a ;  dateDiff('year',a,b) -> year(b) - year(a)
--   round(double, n)        -> round(x::numeric, n)
--   HAVING <alias>          -> HAVING <expression>  (PG cannot reference output aliases there)
--   ORDER BY x DESC         -> ... DESC NULLS LAST  (ClickHouse sorts NULLs last both ways)
--   quantile(0.9)(x)        -> percentile_cont(0.9)   (ClickHouse quantile is approximate)
-- The database is initialised with the C collation so text ordering matches ClickHouse.

-- @q1
SELECT count(*) FROM retail.DimProduct
-- @q2
SELECT sum(SalesAmount) FROM retail.FactOnlineSales
-- @q3
SELECT count(DISTINCT CustomerKey) FROM retail.FactOnlineSales
-- @q4
SELECT ProductName FROM retail.DimProduct WHERE BrandName = 'Contoso'
-- @q5
SELECT pc.ProductCategoryName, count(*) as product_count FROM retail.DimProduct p JOIN retail.DimProductSubcategory ps ON p.ProductSubcategoryKey = ps.ProductSubcategoryKey JOIN retail.DimProductCategory pc ON ps.ProductCategoryKey = pc.ProductCategoryKey GROUP BY pc.ProductCategoryName
-- @q6
SELECT sum(SalesAmount) FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009
-- @q7
-- @ties 1
SELECT ProductName, UnitPrice FROM retail.DimProduct ORDER BY UnitPrice DESC NULLS LAST LIMIT 10
-- @q8
SELECT FirstName, LastName FROM retail.DimEmployee WHERE IsSalesPerson = 'Yes'
-- @q9
SELECT avg(UnitCost) FROM retail.DimProduct
-- @q10
SELECT count(*) FROM retail.DimStore WHERE Status = 'On' OR CloseDate IS NULL
-- @q11
SELECT pc.ProductCategoryName, sum(s.SalesAmount) as total_sales FROM retail.FactOnlineSales s JOIN retail.DimProduct p ON s.ProductKey = p.ProductKey JOIN retail.DimProductSubcategory ps ON p.ProductSubcategoryKey = ps.ProductSubcategoryKey JOIN retail.DimProductCategory pc ON ps.ProductCategoryKey = pc.ProductCategoryKey GROUP BY pc.ProductCategoryName ORDER BY total_sales DESC NULLS LAST
-- @q12
SELECT EXTRACT(MONTH FROM DateKey)::int as month, sum(SalesAmount) as monthly_sales FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2008 GROUP BY month ORDER BY month
-- @q13
SELECT s.StoreName, sum(f.SalesAmount) as total_sales, avg(f.SalesAmount) as avg_order_value, count(*) as num_transactions FROM retail.FactSales f JOIN retail.DimStore s ON f.StoreKey = s.StoreKey GROUP BY s.StoreName
-- @q14
SELECT c.FirstName, c.LastName, sum(s.SalesAmount) as total_spent FROM retail.FactOnlineSales s JOIN retail.DimCustomer c ON s.CustomerKey = c.CustomerKey GROUP BY c.CustomerKey, c.FirstName, c.LastName HAVING sum(s.SalesAmount) > 10000
-- @q15
SELECT g.RegionCountryName, sum(s.SalesAmount) as total_sales FROM retail.FactOnlineSales s JOIN retail.DimCustomer c ON s.CustomerKey = c.CustomerKey JOIN retail.DimGeography g ON c.GeographyKey = g.GeographyKey GROUP BY g.RegionCountryName ORDER BY total_sales DESC NULLS LAST
-- @q16
SELECT EXTRACT(YEAR FROM DateKey)::int as year, sum(SalesAmount) as total_sales FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int IN (2008, 2009) GROUP BY year ORDER BY year
-- @q17
SELECT d.IsWorkDay, sum(s.SalesAmount) as sales, round((sum(s.SalesAmount) * 100.0 / (SELECT sum(SalesAmount) FROM retail.FactOnlineSales))::numeric, 2) as percentage FROM retail.FactOnlineSales s JOIN retail.DimDate d ON s.DateKey = d.DateKey GROUP BY d.IsWorkDay
-- @q18
SELECT p.PromotionType, sum(s.DiscountAmount) as total_discount FROM retail.FactOnlineSales s JOIN retail.DimPromotion p ON s.PromotionKey = p.PromotionKey WHERE p.PromotionKey != 1 GROUP BY p.PromotionType
-- @q19
SELECT c.Gender, avg(s.SalesAmount) as avg_order_value FROM retail.FactOnlineSales s JOIN retail.DimCustomer c ON s.CustomerKey = c.CustomerKey GROUP BY c.Gender
-- @q20
WITH latest AS (SELECT MAX(DateKey) as max_date FROM retail.FactInventory) SELECT p.ProductName, i.OnHandQuantity, i.SafetyStockQuantity FROM retail.FactInventory i JOIN retail.DimProduct p ON i.ProductKey = p.ProductKey JOIN latest ON i.DateKey = latest.max_date WHERE i.OnHandQuantity < i.SafetyStockQuantity
-- @q21
WITH latest AS (SELECT StoreKey, ProductKey, MAX(DateKey) as max_date FROM retail.FactInventory GROUP BY StoreKey, ProductKey) SELECT s.StoreName, p.ProductName, i.OnHandQuantity FROM retail.FactInventory i JOIN latest l ON i.StoreKey = l.StoreKey AND i.ProductKey = l.ProductKey AND i.DateKey = l.max_date JOIN retail.DimStore s ON i.StoreKey = s.StoreKey JOIN retail.DimProduct p ON i.ProductKey = p.ProductKey
-- @q22
WITH customer_sales AS (SELECT c.CustomerKey, c.FirstName, c.LastName, g.RegionCountryName, sum(s.SalesAmount) as total_spent, ROW_NUMBER() OVER (PARTITION BY g.RegionCountryName ORDER BY sum(s.SalesAmount) DESC) as rn FROM retail.FactOnlineSales s JOIN retail.DimCustomer c ON s.CustomerKey = c.CustomerKey JOIN retail.DimGeography g ON c.GeographyKey = g.GeographyKey GROUP BY c.CustomerKey, c.FirstName, c.LastName, g.RegionCountryName) SELECT * FROM customer_sales WHERE rn <= 5
-- @q23
SELECT EXTRACT(MONTH FROM DateKey)::int as month, sum(SalesAmount) as monthly_sales, sum(sum(SalesAmount)) OVER (ORDER BY EXTRACT(MONTH FROM DateKey)::int) as cumulative_sales FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 GROUP BY EXTRACT(MONTH FROM DateKey)::int ORDER BY month
-- @q24
SELECT pc.ProductCategoryName, sum(s.ReturnQuantity) as total_returns, sum(s.SalesQuantity) as total_sales, round(sum(s.ReturnQuantity) * 100.0 / nullif(sum(s.SalesQuantity), 0), 2) as return_rate_pct FROM retail.FactOnlineSales s JOIN retail.DimProduct p ON s.ProductKey = p.ProductKey JOIN retail.DimProductSubcategory ps ON p.ProductSubcategoryKey = ps.ProductSubcategoryKey JOIN retail.DimProductCategory pc ON ps.ProductCategoryKey = pc.ProductCategoryKey GROUP BY pc.ProductCategoryName
-- @q25
SELECT d.NorthAmericaSeason, avg(daily_sales) as avg_daily_sales FROM (SELECT DateKey, sum(SalesAmount) as daily_sales FROM retail.FactOnlineSales GROUP BY DateKey) s JOIN retail.DimDate d ON s.DateKey = d.DateKey GROUP BY d.NorthAmericaSeason ORDER BY avg_daily_sales DESC NULLS LAST LIMIT 1
-- @q26
WITH first_purchase AS (SELECT CustomerKey, MIN(DateKey) as first_date FROM retail.FactOnlineSales GROUP BY CustomerKey) SELECT EXTRACT(QUARTER FROM first_date)::int as quarter, count(*) as new_customers FROM first_purchase WHERE EXTRACT(YEAR FROM first_date)::int = 2007 GROUP BY quarter ORDER BY quarter
-- @q27
-- @ties 3
SELECT ProductName, UnitPrice, UnitCost, round(((UnitPrice - UnitCost) * 100.0 / nullif(UnitPrice, 0))::numeric, 2) as margin_pct FROM retail.DimProduct WHERE UnitPrice > 0 ORDER BY margin_pct DESC NULLS LAST LIMIT 10
-- @q28
SELECT 'Online' as channel, avg(SalesAmount) as avg_transaction FROM retail.FactOnlineSales UNION ALL SELECT 'Store' as channel, avg(SalesAmount) FROM retail.FactSales WHERE channelKey = 1
-- @q29
WITH daily_sales AS (SELECT d.DateKey, d.IsHoliday, sum(s.SalesAmount) as sales FROM retail.FactOnlineSales s JOIN retail.DimDate d ON s.DateKey = d.DateKey GROUP BY d.DateKey, d.IsHoliday), avgs AS (SELECT IsHoliday, avg(sales) as avg_sales FROM daily_sales GROUP BY IsHoliday) SELECT round(((h.avg_sales - r.avg_sales) * 100.0 / r.avg_sales)::numeric, 2) as pct_difference FROM avgs h, avgs r WHERE h.IsHoliday = 'Yes' AND r.IsHoliday = 'No'
-- @q30
SELECT DateKey, daily_sales, avg(daily_sales) OVER (ORDER BY DateKey ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) as moving_avg_7d FROM (SELECT DateKey, sum(SalesAmount) as daily_sales FROM retail.FactOnlineSales WHERE DateKey >= '2009-12-01' AND DateKey <= '2009-12-31' GROUP BY DateKey) d ORDER BY DateKey
-- @q31
SELECT pc.ProductCategoryName, sum(CASE WHEN sp.ScenarioKey = 1 THEN sp.Amount ELSE 0 END) as actual, sum(CASE WHEN sp.ScenarioKey = 2 THEN sp.Amount ELSE 0 END) as budget FROM retail.FactStrategyPlan sp JOIN retail.DimProductCategory pc ON sp.ProductCategoryKey = pc.ProductCategoryKey JOIN retail.DimAccount a ON sp.AccountKey = a.AccountKey WHERE a.AccountName = 'Sale Revenue' AND EXTRACT(YEAR FROM sp.DateKey)::int = 2009 GROUP BY pc.ProductCategoryName
-- @q32
SELECT count(*) as customer_count, sum(total_ltv) as total_ltv FROM (SELECT CustomerKey, sum(SalesAmount) as total_ltv FROM retail.FactOnlineSales GROUP BY CustomerKey HAVING count(DISTINCT EXTRACT(YEAR FROM DateKey)::int) = 3) t
-- @q33
-- @deviation excluded in the paper (ClickHouse memory quota); runs into temp_file_limit here too
-- @note Excluded in the paper (ClickHouse memory quota); kept verbatim apart from count(*).
SELECT p1.ProductName as product1, p2.ProductName as product2, count(*) as times_together FROM retail.FactOnlineSales s1 JOIN retail.FactOnlineSales s2 ON s1.SalesOrderNumber = s2.SalesOrderNumber AND s1.ProductKey < s2.ProductKey JOIN retail.DimProduct p1 ON s1.ProductKey = p1.ProductKey JOIN retail.DimProduct p2 ON s2.ProductKey = p2.ProductKey GROUP BY p1.ProductName, p2.ProductName ORDER BY times_together DESC NULLS LAST LIMIT 20
-- @q34
WITH cogs AS (SELECT ProductKey, sum(TotalCost) as total_cogs FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 GROUP BY ProductKey), avg_inv AS (SELECT ProductKey, avg(OnHandQuantity * UnitCost) as avg_inventory FROM retail.FactInventory WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 GROUP BY ProductKey) SELECT p.ProductName, c.total_cogs, a.avg_inventory, round((c.total_cogs / nullif(a.avg_inventory, 0))::numeric, 2) as turnover_ratio FROM cogs c JOIN avg_inv a ON c.ProductKey = a.ProductKey JOIN retail.DimProduct p ON c.ProductKey = p.ProductKey ORDER BY turnover_ratio DESC NULLS LAST
-- @q35
SELECT e.FirstName, e.LastName, e.Title, m.FirstName as manager_first, m.LastName as manager_last FROM retail.DimEmployee e LEFT JOIN retail.DimEmployee m ON e.ParentEmployeeKey = m.EmployeeKey
-- @q36
WITH max_date AS (SELECT MAX(DateKey) as data_end FROM retail.FactOnlineSales), customer_last AS (SELECT CustomerKey, MAX(DateKey) as last_purchase FROM retail.FactOnlineSales GROUP BY CustomerKey) SELECT CASE WHEN (EXTRACT(YEAR FROM md.data_end)::int * 12 + EXTRACT(MONTH FROM md.data_end)::int) - (EXTRACT(YEAR FROM cl.last_purchase)::int * 12 + EXTRACT(MONTH FROM cl.last_purchase)::int) <= 6 THEN 'Active' WHEN (EXTRACT(YEAR FROM md.data_end)::int * 12 + EXTRACT(MONTH FROM md.data_end)::int) - (EXTRACT(YEAR FROM cl.last_purchase)::int * 12 + EXTRACT(MONTH FROM cl.last_purchase)::int) <= 12 THEN 'At Risk' ELSE 'Churned' END as segment, count(*) as customers FROM customer_last cl, max_date md GROUP BY segment
-- @q37
SELECT s.StoreName, sum(f.SalesAmount) as total_sales, round((PERCENT_RANK() OVER (ORDER BY sum(f.SalesAmount)) * 100)::numeric, 2) as percentile FROM retail.FactSales f JOIN retail.DimStore s ON f.StoreKey = s.StoreKey GROUP BY s.StoreKey, s.StoreName ORDER BY total_sales DESC NULLS LAST
-- @q38
WITH actuals AS (SELECT ProductCategoryKey, sum(Amount) as actual FROM retail.FactStrategyPlan WHERE ScenarioKey = 1 AND DateKey >= '2009-10-01' AND DateKey <= '2009-12-31' AND AccountKey = 4 GROUP BY ProductCategoryKey), forecasts AS (SELECT ProductCategoryKey, sum(Amount) as forecast FROM retail.FactStrategyPlan WHERE ScenarioKey = 3 AND DateKey >= '2009-10-01' AND DateKey <= '2009-12-31' AND AccountKey = 4 GROUP BY ProductCategoryKey) SELECT pc.ProductCategoryName, a.actual, f.forecast, round((abs(a.actual - f.forecast) * 100.0 / nullif(a.actual, 0))::numeric, 2) as error_pct FROM actuals a JOIN forecasts f ON a.ProductCategoryKey = f.ProductCategoryKey JOIN retail.DimProductCategory pc ON a.ProductCategoryKey = pc.ProductCategoryKey
-- @q39
-- This requires identifying new products by AvailableForSaleDate and comparing before/after sales
WITH new_products AS (SELECT ProductSubcategoryKey, MIN(AvailableForSaleDate::date) as first_new FROM retail.DimProduct WHERE AvailableForSaleDate IS NOT NULL GROUP BY ProductSubcategoryKey HAVING MIN(AvailableForSaleDate::date) > '2007-06-01'), existing_sales AS (SELECT p.ProductSubcategoryKey AS ProductSubcategoryKey, CASE WHEN s.DateKey < np.first_new THEN 'before' ELSE 'after' END as period, sum(s.SalesAmount) as sales FROM retail.FactOnlineSales s JOIN retail.DimProduct p ON s.ProductKey = p.ProductKey JOIN new_products np ON p.ProductSubcategoryKey = np.ProductSubcategoryKey WHERE p.AvailableForSaleDate::date < np.first_new GROUP BY p.ProductSubcategoryKey, period) SELECT ps.ProductSubcategoryName, sum(CASE WHEN period = 'before' THEN sales END) as before_sales, sum(CASE WHEN period = 'after' THEN sales END) as after_sales FROM existing_sales es JOIN retail.DimProductSubcategory ps ON es.ProductSubcategoryKey = ps.ProductSubcategoryKey GROUP BY ps.ProductSubcategoryName
-- @q40
-- @note ClickHouse sum() over zero rows returns 0, PostgreSQL NULL; COALESCE keeps 0.
SELECT COALESCE(sum(s.SalesAmount * er.EndOfDayRate), 0) as sales_eur FROM retail.FactOnlineSales s JOIN retail.FactExchangeRate er ON s.DateKey = er.DateKey AND s.CurrencyKey = er.CurrencyKey JOIN retail.DimCurrency c ON er.CurrencyKey = c.CurrencyKey WHERE c.CurrencyName = 'EUR'
-- @q41
-- Ambiguous: assuming last quarter of available data (Q4 2009)
SELECT sum(SalesAmount) FROM retail.FactOnlineSales WHERE DateKey >= '2009-10-01' AND DateKey <= '2009-12-31'
-- @q42
-- Ambiguous: interpreting as sales trend
SELECT EXTRACT(YEAR FROM s.DateKey)::int as year, sum(s.SalesAmount) as sales, sum(s.SalesAmount) - lag(sum(s.SalesAmount), 1, 0) OVER (ORDER BY EXTRACT(YEAR FROM s.DateKey)::int) as yoy_change FROM retail.FactOnlineSales s JOIN retail.DimProduct p ON s.ProductKey = p.ProductKey JOIN retail.DimProductSubcategory ps ON p.ProductSubcategoryKey = ps.ProductSubcategoryKey JOIN retail.DimProductCategory pc ON ps.ProductCategoryKey = pc.ProductCategoryKey WHERE pc.ProductCategoryName = 'Cameras and camcorders' GROUP BY EXTRACT(YEAR FROM s.DateKey)::int ORDER BY year
-- @q43
-- Interpreting gross margin as (SalesAmount - TotalCost), region as continent
SELECT g.ContinentName as region, sum(s.SalesAmount) - sum(s.TotalCost) as gross_margin FROM retail.FactOnlineSales s JOIN retail.DimCustomer c ON s.CustomerKey = c.CustomerKey JOIN retail.DimGeography g ON c.GeographyKey = g.GeographyKey GROUP BY g.ContinentName
-- @q44
-- @deviation first-year growth is NULL instead of inf (see note)
-- Interpreting as year-over-year growth
-- @note ClickHouse returns inf for the first year (x / 0.0); PostgreSQL raises on float division by zero, so the denominator is wrapped in nullif and the first year is NULL.
SELECT EXTRACT(YEAR FROM DateKey)::int as year, sum(SalesAmount) as sales, round(((sum(SalesAmount) - lag(sum(SalesAmount), 1, 0) OVER (ORDER BY EXTRACT(YEAR FROM DateKey)::int)) * 100.0 / nullif(lag(sum(SalesAmount), 1, 0) OVER (ORDER BY EXTRACT(YEAR FROM DateKey)::int), 0))::numeric, 2) as growth_pct FROM retail.FactOnlineSales GROUP BY EXTRACT(YEAR FROM DateKey)::int ORDER BY year
-- @q45
SELECT s.StoreName, sum(f.SalesAmount) as total_sales, s.SellingAreaSize, round((sum(f.SalesAmount) / nullif(s.SellingAreaSize, 0))::numeric, 2) as sales_per_sqft FROM retail.FactSales f JOIN retail.DimStore s ON f.StoreKey = s.StoreKey WHERE s.SellingAreaSize IS NOT NULL AND s.SellingAreaSize > 0 GROUP BY s.StoreKey, s.StoreName, s.SellingAreaSize ORDER BY sales_per_sqft DESC NULLS LAST LIMIT 10
-- @q46
-- Interpreting segment as education level, profitable as total profit
SELECT c.Education as segment, sum(s.SalesAmount - s.TotalCost) as profit FROM retail.FactOnlineSales s JOIN retail.DimCustomer c ON s.CustomerKey = c.CustomerKey WHERE c.Education IS NOT NULL GROUP BY c.Education ORDER BY profit DESC NULLS LAST LIMIT 1
-- @q47
SELECT s.StoreName, avg(i.DownTime) as avg_downtime_minutes FROM retail.FactITSLA i JOIN retail.DimStore s ON i.StoreKey = s.StoreKey WHERE i.DownTime IS NOT NULL GROUP BY s.StoreKey, s.StoreName ORDER BY avg_downtime_minutes DESC NULLS LAST
-- @q48
-- Complex: comparing stores with high outages vs low outages
WITH store_outages AS (SELECT StoreKey, count(*) as outage_count, sum(DownTime) as total_downtime FROM retail.FactITSLA GROUP BY StoreKey), store_sales AS (SELECT StoreKey, sum(SalesAmount) as total_sales FROM retail.FactSales GROUP BY StoreKey) SELECT CASE WHEN so.total_downtime > 1000 THEN 'High Downtime' ELSE 'Low Downtime' END as downtime_group, avg(ss.total_sales) as avg_sales, count(*) as store_count FROM store_outages so JOIN store_sales ss ON so.StoreKey = ss.StoreKey GROUP BY downtime_group
-- @q49
-- Using FactStrategyPlan which has EntityKey linkage
WITH RECURSIVE entity_tree AS (SELECT EntityKey, EntityName, ParentEntityKey, EntityKey as root_key FROM retail.DimEntity WHERE ParentEntityKey IS NULL UNION ALL SELECT e.EntityKey, e.EntityName, e.ParentEntityKey, t.root_key FROM retail.DimEntity e JOIN entity_tree t ON e.ParentEntityKey = t.EntityKey) SELECT et.EntityName, sum(sp.Amount) as total_amount FROM retail.FactStrategyPlan sp JOIN entity_tree et ON sp.EntityKey = et.EntityKey WHERE sp.ScenarioKey = 1 AND sp.AccountKey = 4 GROUP BY et.EntityName
-- @q50
WITH stores_both_years AS (SELECT DISTINCT StoreKey FROM retail.FactSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2008 INTERSECT SELECT DISTINCT StoreKey FROM retail.FactSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009), sales_2008 AS (SELECT sum(SalesAmount) as sales FROM retail.FactSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2008 AND StoreKey IN (SELECT StoreKey FROM stores_both_years)), sales_2009 AS (SELECT sum(SalesAmount) as sales FROM retail.FactSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 AND StoreKey IN (SELECT StoreKey FROM stores_both_years)) SELECT s9.sales as sales_2009, s8.sales as sales_2008, round(((s9.sales - s8.sales) * 100.0 / s8.sales)::numeric, 2) as growth_pct FROM sales_2009 s9, sales_2008 s8
-- @q51
SELECT count(*) AS total_stores FROM retail.DimStore
-- @q52
SELECT max(UnitPrice) AS max_unit_price FROM retail.DimProduct
-- @q53
SELECT count(DISTINCT SalesOrderNumber) AS total_orders FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009
-- @q54
SELECT sum(ReturnAmount) AS total_return_amount FROM retail.FactOnlineSales
-- @q55
SELECT count(*) AS female_customers FROM retail.DimCustomer WHERE Gender = 'F'
-- @q56
SELECT avg(YearlyIncome) AS avg_yearly_income FROM retail.DimCustomer
-- @q57
SELECT count(DISTINCT BrandName) AS total_brands FROM retail.DimProduct
-- @q58
SELECT max(SalesAmount) AS max_line_sales_amount FROM retail.FactOnlineSales
-- @q59
SELECT count(*) AS holiday_days FROM retail.DimDate WHERE IsHoliday = 'Yes'
-- @q60
SELECT count(*) AS production_employees FROM retail.DimEmployee WHERE DepartmentName = 'Production'
-- @q61
SELECT psc.ProductSubcategoryName, sum(f.SalesAmount) AS total_sales FROM retail.FactOnlineSales f JOIN retail.DimProduct p ON f.ProductKey = p.ProductKey JOIN retail.DimProductSubcategory psc ON p.ProductSubcategoryKey = psc.ProductSubcategoryKey WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2009 GROUP BY psc.ProductSubcategoryName HAVING sum(f.SalesAmount) > 1000000 ORDER BY total_sales DESC NULLS LAST
-- @q62
SELECT p.BrandName, sum(f.SalesAmount) AS revenue, sum(f.SalesQuantity) AS units FROM retail.FactOnlineSales f JOIN retail.DimProduct p ON f.ProductKey = p.ProductKey WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2008 GROUP BY p.BrandName HAVING sum(f.SalesQuantity) >= 500 ORDER BY revenue DESC NULLS LAST
-- @q63
SELECT c.Occupation, sum(f.SalesAmount) / nullif(count(DISTINCT f.SalesOrderNumber), 0) AS avg_order_value, count(DISTINCT f.SalesOrderNumber) AS orders FROM retail.FactOnlineSales f JOIN retail.DimCustomer c ON f.CustomerKey = c.CustomerKey GROUP BY c.Occupation ORDER BY avg_order_value DESC NULLS LAST
-- @q64
SELECT EXTRACT(MONTH FROM DateKey)::int AS month, sum(SalesAmount) - sum(TotalCost) AS gross_margin, (sum(SalesAmount) - sum(TotalCost)) * 100.0 / nullif(sum(SalesAmount), 0) AS margin_pct FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 GROUP BY month ORDER BY month
-- @q65
SELECT s.StoreManager, s.StoreName, sum(f.SalesAmount) AS total_sales FROM retail.FactSales f JOIN retail.DimStore s ON f.StoreKey = s.StoreKey GROUP BY s.StoreManager, s.StoreName HAVING sum(f.SalesAmount) > 5000000 ORDER BY total_sales DESC NULLS LAST LIMIT 50
-- @q66
SELECT EXTRACT(QUARTER FROM DateKey)::int AS quarter, sum(DiscountAmount) AS total_discount, count(DISTINCT SalesOrderNumber) AS discounted_orders FROM retail.FactOnlineSales WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 AND PromotionKey <> 1 GROUP BY quarter ORDER BY quarter
-- @q67
SELECT p.ColorName, sum(f.SalesAmount) / nullif(sum(f.SalesQuantity), 0) AS avg_realized_price, sum(f.SalesQuantity) AS units FROM retail.FactOnlineSales f JOIN retail.DimProduct p ON f.ProductKey = p.ProductKey GROUP BY p.ColorName HAVING sum(f.SalesQuantity) >= 1000 ORDER BY avg_realized_price DESC NULLS LAST LIMIT 20
-- @q68
SELECT d.IsHoliday, count(DISTINCT f.SalesOrderNumber) AS orders, sum(f.SalesAmount) AS revenue FROM retail.FactOnlineSales f JOIN retail.DimDate d ON f.DateKey = d.DateKey WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2009 GROUP BY d.IsHoliday
-- @q69
SELECT pr.PromotionCategory, sum(f.SalesAmount) AS revenue, sum(f.DiscountAmount) AS total_discount, sum(f.DiscountAmount) * 100.0 / nullif(sum(f.SalesAmount + f.DiscountAmount), 0) AS avg_discount_pct FROM retail.FactOnlineSales f JOIN retail.DimPromotion pr ON f.PromotionKey = pr.PromotionKey WHERE f.PromotionKey <> 1 GROUP BY pr.PromotionCategory ORDER BY revenue DESC NULLS LAST
-- @q70
SELECT s.StoreType, sum(f.SalesAmount) AS revenue, avg(s.SellingAreaSize) AS avg_area, count(DISTINCT f.StoreKey) AS stores FROM retail.FactSales f JOIN retail.DimStore s ON f.StoreKey = s.StoreKey GROUP BY s.StoreType ORDER BY revenue DESC NULLS LAST
-- @q71
WITH ranked AS (
  SELECT pc.ProductCategoryName AS category, psc.ProductSubcategoryName AS subcategory,
         sum(f.SalesAmount) AS sales,
         row_number() OVER (PARTITION BY pc.ProductCategoryName ORDER BY sum(f.SalesAmount) DESC) AS rn
  FROM retail.FactOnlineSales f
  JOIN retail.DimProduct p ON f.ProductKey = p.ProductKey
  JOIN retail.DimProductSubcategory psc ON p.ProductSubcategoryKey = psc.ProductSubcategoryKey
  JOIN retail.DimProductCategory pc ON psc.ProductCategoryKey = pc.ProductCategoryKey
  WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2009
  GROUP BY pc.ProductCategoryName, psc.ProductSubcategoryName
)
SELECT category, subcategory, round(sales::numeric, 2) AS sales_amount
FROM ranked WHERE rn = 1
ORDER BY sales_amount DESC NULLS LAST
-- @q72
SELECT EXTRACT(YEAR FROM DateKey)::int AS year, EXTRACT(QUARTER FROM DateKey)::int AS quarter,
       round(sum(SalesAmount)::numeric, 2) AS quarterly_sales,
       round((sum(sum(SalesAmount)) OVER (ORDER BY EXTRACT(YEAR FROM DateKey)::int, EXTRACT(QUARTER FROM DateKey)::int ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))::numeric, 2) AS cumulative_sales
FROM retail.FactOnlineSales
GROUP BY EXTRACT(YEAR FROM DateKey)::int, EXTRACT(QUARTER FROM DateKey)::int
ORDER BY year, quarter
-- @q73
WITH latest AS (
  SELECT StoreKey, ProductKey, MAX(DateKey) AS max_date
  FROM retail.FactInventory
  GROUP BY StoreKey, ProductKey
)
SELECT p.ProductName,
       sum(i.OnHandQuantity) AS total_on_hand,
       sum(i.SafetyStockQuantity) AS total_safety,
       sum(i.OnHandQuantity) - sum(i.SafetyStockQuantity) AS gap
FROM retail.FactInventory i
JOIN latest l ON i.StoreKey = l.StoreKey AND i.ProductKey = l.ProductKey AND i.DateKey = l.max_date
JOIN retail.DimProduct p ON i.ProductKey = p.ProductKey
GROUP BY p.ProductName
ORDER BY gap DESC NULLS LAST
LIMIT 10
-- @q74
WITH prod_store AS (
  SELECT f.StoreKey, f.ProductKey, sum(f.SalesAmount) AS sales
  FROM retail.FactOnlineSales f
  WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2009
  GROUP BY f.StoreKey, f.ProductKey
),
ranked AS (
  SELECT StoreKey, ProductKey, sales,
         row_number() OVER (PARTITION BY StoreKey ORDER BY sales DESC) AS rn,
         sum(sales) OVER (PARTITION BY StoreKey) AS store_total
  FROM prod_store
)
SELECT s.StoreName, p.ProductName, round(r.sales::numeric, 2) AS product_sales,
       round((r.sales * 100.0 / nullif(r.store_total, 0))::numeric, 2) AS pct_of_store
FROM ranked r
JOIN retail.DimStore s ON r.StoreKey = s.StoreKey
JOIN retail.DimProduct p ON r.ProductKey = p.ProductKey
WHERE r.rn <= 3
ORDER BY s.StoreName, r.rn
LIMIT 50
-- @q75
WITH yearly AS (
  SELECT CustomerKey,
         COALESCE(sum(SalesAmount) FILTER (WHERE EXTRACT(YEAR FROM DateKey)::int = 2008), 0) AS y2008,
         COALESCE(sum(SalesAmount) FILTER (WHERE EXTRACT(YEAR FROM DateKey)::int = 2009), 0) AS y2009
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
)
SELECT c.FirstName, c.LastName,
       round(y.y2008::numeric, 2) AS spend_2008,
       round(y.y2009::numeric, 2) AS spend_2009,
       round(((y.y2009 - y.y2008) * 100.0 / nullif(y.y2008, 0))::numeric, 2) AS pct_growth
FROM yearly y
JOIN retail.DimCustomer c ON y.CustomerKey = c.CustomerKey
WHERE y.y2008 > 0 AND (y.y2009 - y.y2008) / nullif(y.y2008, 0) > 0.5
ORDER BY pct_growth DESC NULLS LAST
LIMIT 25
-- @q76
WITH cust_orders AS (
  SELECT CustomerKey, count(DISTINCT SalesOrderNumber) AS n_orders
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
)
SELECT pc.ProductCategoryName AS category,
       round((COALESCE(sum(f.SalesAmount) FILTER (WHERE co.n_orders >= 2), 0) * 100.0 / nullif(sum(f.SalesAmount), 0))::numeric, 2) AS pct_repeat_customer_sales
FROM retail.FactOnlineSales f
JOIN cust_orders co ON f.CustomerKey = co.CustomerKey
JOIN retail.DimProduct p ON f.ProductKey = p.ProductKey
JOIN retail.DimProductSubcategory psc ON p.ProductSubcategoryKey = psc.ProductSubcategoryKey
JOIN retail.DimProductCategory pc ON psc.ProductCategoryKey = pc.ProductCategoryKey
GROUP BY category
ORDER BY pct_repeat_customer_sales DESC NULLS LAST
-- @q77
WITH monthly AS (
  SELECT EXTRACT(MONTH FROM DateKey)::int AS month, sum(SalesAmount) AS sales
  FROM retail.FactOnlineSales
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009
  GROUP BY month
)
SELECT month,
       round(sales::numeric, 2) AS sales,
       round((lag(sales, 1, 0) OVER (ORDER BY month))::numeric, 2) AS prev_month_sales,
       round(((sales - lag(sales, 1, 0) OVER (ORDER BY month)) * 100.0 / nullif(lag(sales, 1, 0) OVER (ORDER BY month), 0))::numeric, 2) AS mom_growth_pct
FROM monthly
ORDER BY month
-- @q78
WITH promo AS (
  SELECT pr.PromotionCategory AS category, sum(f.DiscountAmount) AS disc
  FROM retail.FactOnlineSales f
  JOIN retail.DimPromotion pr ON f.PromotionKey = pr.PromotionKey
  WHERE f.PromotionKey != 1 AND EXTRACT(YEAR FROM f.DateKey)::int = 2009
  GROUP BY category
)
SELECT category,
       round(disc::numeric, 2) AS discount_total,
       rank() OVER (ORDER BY disc DESC) AS rnk,
       round((disc * 100.0 / nullif(sum(disc) OVER (), 0))::numeric, 2) AS pct_of_total
FROM promo
ORDER BY rnk
-- @q79
-- @ties 3
WITH actuals AS (
  SELECT StoreKey, sum(SalesAmount) AS actual_sales
  FROM retail.FactSales
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009
  GROUP BY StoreKey
),
quotas AS (
  SELECT StoreKey, sum(SalesAmountQuota) AS quota
  FROM retail.FactSalesQuota
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 AND ScenarioKey = 1
  GROUP BY StoreKey
)
SELECT s.StoreName,
       round(a.actual_sales::numeric, 2) AS actual_sales,
       round(q.quota::numeric, 2) AS quota,
       round((a.actual_sales * 100.0 / nullif(q.quota, 0))::numeric, 2) AS attainment_pct
FROM actuals a
JOIN quotas q ON a.StoreKey = q.StoreKey
JOIN retail.DimStore s ON a.StoreKey = s.StoreKey
WHERE q.quota > 0
ORDER BY attainment_pct DESC NULLS LAST
LIMIT 10
-- @q80
WITH cust AS (
  SELECT CustomerKey, MIN(DateKey) AS first_d, MAX(DateKey) AS last_d
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
)
SELECT g.RegionCountryName AS country,
       count(*) AS n_customers,
       round(avg(cust.last_d - cust.first_d), 1) AS avg_active_days
FROM cust
JOIN retail.DimCustomer c ON cust.CustomerKey = c.CustomerKey
JOIN retail.DimGeography g ON c.GeographyKey = g.GeographyKey
WHERE cust.first_d != cust.last_d
GROUP BY country
ORDER BY avg_active_days DESC NULLS LAST
LIMIT 20
-- @q81
WITH first_purchase AS (
  SELECT CustomerKey, EXTRACT(YEAR FROM MIN(DateKey))::int AS cohort_year
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
),
customer_spend AS (
  SELECT CustomerKey, SUM(SalesAmount) AS lifetime_spend
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
),
joined AS (
  SELECT f.cohort_year, c.lifetime_spend
  FROM first_purchase f
  JOIN customer_spend c USING (CustomerKey)
)
SELECT cohort_year,
       count(*) AS customers_in_cohort,
       round(avg(lifetime_spend)::numeric, 2) AS avg_ltv,
       round(sum(lifetime_spend)::numeric, 2) AS total_ltv
FROM joined
GROUP BY cohort_year
ORDER BY cohort_year
-- @q82
WITH reports AS (
  SELECT ParentEmployeeKey AS mgr_key,
         count(*) AS direct_reports,
         avg(2009 - EXTRACT(YEAR FROM HireDate::date)::int) AS avg_tenure_years
  FROM retail.DimEmployee
  WHERE ParentEmployeeKey IS NOT NULL AND HireDate IS NOT NULL
  GROUP BY mgr_key
)
SELECT m.FirstName || ' ' || m.LastName AS manager_name,
       m.Title,
       r.direct_reports,
       round(r.avg_tenure_years, 1) AS avg_team_tenure_years
FROM reports r
JOIN retail.DimEmployee m ON m.EmployeeKey = r.mgr_key::int
ORDER BY r.direct_reports DESC NULLS LAST, manager_name
LIMIT 25
-- @q83
WITH first_yr AS (
  SELECT CustomerKey, EXTRACT(YEAR FROM MIN(DateKey))::int AS first_year
  FROM retail.FactOnlineSales GROUP BY CustomerKey
),
cohort_2007 AS (
  SELECT CustomerKey FROM first_yr WHERE first_year = 2007
),
active AS (
  SELECT CustomerKey, EXTRACT(YEAR FROM DateKey)::int AS y
  FROM retail.FactOnlineSales
  WHERE CustomerKey IN (SELECT CustomerKey FROM cohort_2007)
  GROUP BY CustomerKey, y
)
SELECT (SELECT count(*) FROM cohort_2007) AS cohort_size_2007,
       count(DISTINCT CustomerKey) FILTER (WHERE y = 2008) AS returned_2008,
       round(count(DISTINCT CustomerKey) FILTER (WHERE y = 2008) * 100.0 / (SELECT count(*) FROM cohort_2007), 2) AS retention_2008_pct,
       count(DISTINCT CustomerKey) FILTER (WHERE y = 2008 AND CustomerKey IN (
           SELECT CustomerKey FROM active WHERE y = 2009
       )) AS returned_2008_and_2009,
       round(count(DISTINCT CustomerKey) FILTER (WHERE y = 2009) * 100.0 / (SELECT count(*) FROM cohort_2007), 2) AS retention_2009_pct
FROM active
-- @q84
WITH prod_rev AS (
  SELECT ProductKey, SUM(SalesAmount) AS rev
  FROM retail.FactOnlineSales
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009
  GROUP BY ProductKey
),
ranked AS (
  SELECT ProductKey, rev,
         SUM(rev) OVER (ORDER BY rev DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_rev,
         SUM(rev) OVER () AS total_rev
  FROM prod_rev
),
classed AS (
  SELECT ProductKey,
         CASE WHEN cum_rev / total_rev <= 0.80 THEN 'A'
              WHEN cum_rev / total_rev <= 0.95 THEN 'B'
              ELSE 'C' END AS abc_class,
         rev
  FROM ranked
)
SELECT abc_class,
       count(*) AS product_count,
       round(sum(rev)::numeric, 2) AS revenue,
       round((sum(rev) * 100.0 / (SELECT sum(rev) FROM prod_rev))::numeric, 2) AS pct_of_revenue
FROM classed
GROUP BY abc_class
ORDER BY abc_class
-- @q85
-- @ties 0,4
WITH quotas AS (
  SELECT StoreKey, SUM(SalesAmountQuota) AS quota_amt
  FROM retail.FactSalesQuota
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 AND ScenarioKey = 2
  GROUP BY StoreKey
),
actuals AS (
  SELECT StoreKey, SUM(SalesAmount) AS actual_amt
  FROM retail.FactSales
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009
  GROUP BY StoreKey
),
perf AS (
  SELECT s.StoreName, a.actual_amt, q.quota_amt,
         round((a.actual_amt * 100.0 / nullif(q.quota_amt, 0))::numeric, 2) AS attainment_pct
  FROM actuals a
  JOIN quotas q USING (StoreKey)
  JOIN retail.DimStore s ON s.StoreKey = a.StoreKey
  WHERE q.quota_amt > 0
)
SELECT * FROM (
  (SELECT 'TOP' AS bucket, StoreName, actual_amt, quota_amt, attainment_pct FROM perf ORDER BY attainment_pct DESC NULLS LAST LIMIT 5)
  UNION ALL
  (SELECT 'BOTTOM' AS bucket, StoreName, actual_amt, quota_amt, attainment_pct FROM perf ORDER BY attainment_pct ASC LIMIT 5)
) t ORDER BY bucket, attainment_pct DESC NULLS LAST
-- @q86
WITH enriched AS (
  SELECT psc.ProductSubcategoryName,
         p.DiscountPercent,
         fos.SalesQuantity
  FROM retail.FactOnlineSales fos
  JOIN retail.DimPromotion p ON p.PromotionKey = fos.PromotionKey
  JOIN retail.DimProduct dp ON dp.ProductKey = fos.ProductKey
  JOIN retail.DimProductSubcategory psc ON dp.ProductSubcategoryKey::int = psc.ProductSubcategoryKey
  WHERE EXTRACT(YEAR FROM fos.DateKey)::int = 2009 AND p.DiscountPercent IS NOT NULL
),
stats AS (
  SELECT ProductSubcategoryName,
         corr(DiscountPercent, SalesQuantity) AS discount_qty_corr,
         count(*) AS observations
  FROM enriched
  GROUP BY ProductSubcategoryName
  HAVING count(*) > 100
)
SELECT ProductSubcategoryName,
       round(discount_qty_corr::numeric, 3) AS corr_discount_vs_qty,
       observations
FROM stats
ORDER BY discount_qty_corr DESC NULLS LAST
LIMIT 20
-- @q87
WITH latest AS (
  SELECT StoreKey, ProductKey, MAX(DateKey) AS max_date
  FROM retail.FactInventory GROUP BY StoreKey, ProductKey
),
current_inv AS (
  SELECT i.ProductKey, SUM(i.OnHandQuantity) AS on_hand
  FROM retail.FactInventory i
  JOIN latest l ON i.StoreKey = l.StoreKey AND i.ProductKey = l.ProductKey AND i.DateKey = l.max_date
  GROUP BY i.ProductKey
),
recent_sales AS (
  SELECT ProductKey,
         SUM(SalesQuantity) / 90.0 AS avg_daily_qty
  FROM retail.FactOnlineSales
  WHERE DateKey BETWEEN DATE '2009-10-03' AND DATE '2009-12-31'
  GROUP BY ProductKey
  HAVING SUM(SalesQuantity) / 90.0 > 0
)
SELECT p.ProductName,
       c.on_hand,
       round(r.avg_daily_qty, 2) AS avg_daily_qty,
       round(c.on_hand / r.avg_daily_qty, 1) AS days_of_coverage
FROM current_inv c
JOIN recent_sales r USING (ProductKey)
JOIN retail.DimProduct p ON p.ProductKey = c.ProductKey
ORDER BY days_of_coverage ASC
LIMIT 20
-- @q88
-- @ties 1
WITH cust_q AS (
  SELECT CustomerKey, EXTRACT(QUARTER FROM DateKey)::int AS q
  FROM retail.FactOnlineSales
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009
  GROUP BY CustomerKey, q
),
loyal AS (
  SELECT CustomerKey
  FROM cust_q GROUP BY CustomerKey HAVING count(distinct q) = 4
),
spend AS (
  SELECT CustomerKey, SUM(SalesAmount) AS spend_2009
  FROM retail.FactOnlineSales
  WHERE EXTRACT(YEAR FROM DateKey)::int = 2009 AND CustomerKey IN (SELECT CustomerKey FROM loyal)
  GROUP BY CustomerKey
)
SELECT c.FirstName || ' ' || c.LastName AS customer_name,
       round(s.spend_2009::numeric, 2) AS total_spend_2009
FROM spend s
JOIN retail.DimCustomer c ON c.CustomerKey = s.CustomerKey
ORDER BY s.spend_2009 DESC NULLS LAST
LIMIT 20
-- @q89
WITH cust_recency AS (
  SELECT CustomerKey,
         MAX(DateKey) AS last_purchase,
         SUM(SalesAmount) AS total_spend,
         count(DISTINCT ProductKey) AS distinct_products
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
),
segmented AS (
  SELECT *,
         (2009 * 12 + 12) - (EXTRACT(YEAR FROM last_purchase)::int * 12 + EXTRACT(MONTH FROM last_purchase)::int) AS months_inactive,
         CASE
           WHEN (2009 * 12 + 12) - (EXTRACT(YEAR FROM last_purchase)::int * 12 + EXTRACT(MONTH FROM last_purchase)::int) <= 6 THEN 'Active'
           WHEN (2009 * 12 + 12) - (EXTRACT(YEAR FROM last_purchase)::int * 12 + EXTRACT(MONTH FROM last_purchase)::int) <= 12 THEN 'At Risk'
           ELSE 'Churned'
         END AS segment
  FROM cust_recency
)
SELECT segment,
       count(*) AS customers,
       round(avg(total_spend)::numeric, 2) AS avg_spend,
       round(avg(distinct_products), 1) AS avg_distinct_products
FROM segmented
GROUP BY segment
ORDER BY CASE segment WHEN 'Active' THEN 1 WHEN 'At Risk' THEN 2 ELSE 3 END
-- @q90
WITH yearly AS (
  SELECT pc.ProductCategoryName AS category,
         EXTRACT(YEAR FROM fos.DateKey)::int AS y,
         SUM(fos.SalesAmount) AS rev,
         SUM(fos.TotalCost) AS cost
  FROM retail.FactOnlineSales fos
  JOIN retail.DimProduct p ON p.ProductKey = fos.ProductKey
  JOIN retail.DimProductSubcategory psc ON p.ProductSubcategoryKey::int = psc.ProductSubcategoryKey
  JOIN retail.DimProductCategory pc ON pc.ProductCategoryKey = psc.ProductCategoryKey
  GROUP BY category, y
),
margins AS (
  SELECT category, y, (rev - cost) * 100.0 / nullif(rev, 0) AS margin_pct FROM yearly
),
pivoted AS (
  SELECT category,
         max(margin_pct) FILTER (WHERE y = 2007) AS m_2007,
         max(margin_pct) FILTER (WHERE y = 2008) AS m_2008,
         max(margin_pct) FILTER (WHERE y = 2009) AS m_2009
  FROM margins GROUP BY category
)
SELECT category,
       round(m_2007::numeric, 2) AS margin_2007_pct,
       round(m_2008::numeric, 2) AS margin_2008_pct,
       round(m_2009::numeric, 2) AS margin_2009_pct,
       round((m_2008 - m_2007)::numeric, 2) AS delta_08_07_pp,
       round((m_2009 - m_2008)::numeric, 2) AS delta_09_08_pp
FROM pivoted
ORDER BY m_2009 DESC NULLS LAST
-- @q91
SELECT corr(c.YearlyIncome, cust_spend.total_spend) AS income_spend_correlation,
       count(*) AS customers_analyzed
FROM (
  SELECT CustomerKey, sum(SalesAmount) AS total_spend
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
) cust_spend
JOIN retail.DimCustomer c ON c.CustomerKey = cust_spend.CustomerKey
WHERE c.YearlyIncome IS NOT NULL
-- @q92
SELECT p.BrandName,
       sum(f.SalesAmount) AS revenue,
       sum(f.SalesAmount - f.TotalCost) AS gross_margin_dollars,
       sum(f.SalesAmount - f.TotalCost) / nullif(sum(f.SalesAmount), 0) * 100 AS gross_margin_pct
FROM retail.FactOnlineSales f
JOIN retail.DimProduct p ON p.ProductKey = f.ProductKey
WHERE f.DateKey BETWEEN DATE '2009-10-01' AND DATE '2009-12-31'
  AND p.BrandName IS NOT NULL
GROUP BY p.BrandName
ORDER BY gross_margin_dollars DESC NULLS LAST
LIMIT 10
-- @q93
WITH promo_stats AS (
  SELECT f.PromotionKey,
         dp.DiscountPercent,
         sum(f.SalesQuantity) AS units_sold,
         sum(f.SalesAmount) AS revenue
  FROM retail.FactOnlineSales f
  JOIN retail.DimPromotion dp ON dp.PromotionKey = f.PromotionKey
  WHERE f.PromotionKey <> 1 AND dp.DiscountPercent IS NOT NULL
  GROUP BY f.PromotionKey, dp.DiscountPercent
)
SELECT corr(DiscountPercent, units_sold) AS discount_qty_correlation,
       corr(DiscountPercent, revenue) AS discount_revenue_correlation,
       count(*) AS promos_analyzed
FROM promo_stats
-- @q94
SELECT s.StoreName,
       COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 3), 0) AS q3_sales,
       COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 4), 0) AS q4_sales,
       (COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 4), 0) - COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 3), 0))
         * 100.0 / nullif(COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 3), 0), 0) AS qoq_pct_change
FROM retail.FactSales f
JOIN retail.DimStore s ON s.StoreKey = f.StoreKey
WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2009
GROUP BY s.StoreKey, s.StoreName
HAVING COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 3), 0) > 0
   AND (COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 4), 0) - COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 3), 0))
         * 100.0 / nullif(COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(QUARTER FROM f.DateKey)::int = 3), 0), 0) < 0
ORDER BY qoq_pct_change ASC
LIMIT 20
-- @q95
SELECT pc.ProductCategoryName,
       COALESCE(sum(f.SalesAmount) FILTER (WHERE f.PromotionKey <> 1), 0) AS promo_revenue,
       COALESCE(sum(f.SalesAmount) FILTER (WHERE f.PromotionKey = 1), 0) AS full_price_revenue,
       COALESCE(sum(f.SalesAmount) FILTER (WHERE f.PromotionKey <> 1), 0) / nullif(COALESCE(sum(f.SalesAmount) FILTER (WHERE f.PromotionKey = 1), 0), 0) AS promo_to_fullprice_ratio,
       sum(f.DiscountAmount) AS total_discount_given
FROM retail.FactOnlineSales f
JOIN retail.DimProduct p ON p.ProductKey = f.ProductKey
JOIN retail.DimProductSubcategory ps ON ps.ProductSubcategoryKey = p.ProductSubcategoryKey
JOIN retail.DimProductCategory pc ON pc.ProductCategoryKey = ps.ProductCategoryKey
GROUP BY pc.ProductCategoryName
ORDER BY promo_to_fullprice_ratio DESC NULLS LAST
-- @q96
WITH first_year AS (
  SELECT CustomerKey, EXTRACT(YEAR FROM min(DateKey))::int AS first_year
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
),
years_active AS (
  SELECT CustomerKey, count(DISTINCT EXTRACT(YEAR FROM DateKey)::int) AS years_with_activity
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
)
SELECT fy.first_year AS cohort_year,
       count(*) AS cohort_size,
       count(*) FILTER (WHERE ya.years_with_activity > 1) AS returning_customers,
       count(*) FILTER (WHERE ya.years_with_activity > 1) * 100.0 / count(*) AS retention_pct
FROM first_year fy
JOIN years_active ya ON ya.CustomerKey = fy.CustomerKey
GROUP BY fy.first_year
ORDER BY cohort_year
-- @q97
SELECT p.ProductName,
       COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int <= 6), 0) AS h1_sales,
       COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int >= 7), 0) AS h2_sales,
       (COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int >= 7), 0) - COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int <= 6), 0))
         * 100.0 / nullif(COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int <= 6), 0), 0) AS h2_vs_h1_pct
FROM retail.FactOnlineSales f
JOIN retail.DimProduct p ON p.ProductKey = f.ProductKey
WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2009
GROUP BY p.ProductKey, p.ProductName
HAVING COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int <= 6), 0) >= 1000
   AND COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int >= 7), 0) > COALESCE(sum(f.SalesAmount) FILTER (WHERE EXTRACT(MONTH FROM f.DateKey)::int <= 6), 0)
ORDER BY h2_vs_h1_pct DESC NULLS LAST
LIMIT 15
-- @q98
WITH cat_q AS (
  SELECT pc.ProductCategoryName,
         EXTRACT(QUARTER FROM f.DateKey)::int AS q,
         sum(f.SalesAmount) AS q_sales
  FROM retail.FactOnlineSales f
  JOIN retail.DimProduct p ON p.ProductKey = f.ProductKey
  JOIN retail.DimProductSubcategory ps ON ps.ProductSubcategoryKey = p.ProductSubcategoryKey
  JOIN retail.DimProductCategory pc ON pc.ProductCategoryKey = ps.ProductCategoryKey
  WHERE EXTRACT(YEAR FROM f.DateKey)::int = 2009
  GROUP BY pc.ProductCategoryName, q
)
SELECT ProductCategoryName,
       max(q_sales) / sum(q_sales) * 100 AS peak_quarter_share_pct,
       (array_agg(q ORDER BY q_sales DESC))[1] AS peak_quarter
FROM cat_q
GROUP BY ProductCategoryName
ORDER BY peak_quarter_share_pct DESC NULLS LAST
-- @q99
WITH daily AS (
  SELECT pc.ProductCategoryName,
         f.DateKey,
         d.IsWorkDay,
         sum(f.SalesAmount) AS day_sales
  FROM retail.FactOnlineSales f
  JOIN retail.DimDate d ON d.DateKey = f.DateKey
  JOIN retail.DimProduct p ON p.ProductKey = f.ProductKey
  JOIN retail.DimProductSubcategory ps ON ps.ProductSubcategoryKey = p.ProductSubcategoryKey
  JOIN retail.DimProductCategory pc ON pc.ProductCategoryKey = ps.ProductCategoryKey
  GROUP BY pc.ProductCategoryName, f.DateKey, d.IsWorkDay
)
SELECT ProductCategoryName,
       avg(day_sales) FILTER (WHERE IsWorkDay = 'WeekEnd') AS avg_weekend_day_sales,
       avg(day_sales) FILTER (WHERE IsWorkDay = 'WorkDay') AS avg_weekday_sales,
       (avg(day_sales) FILTER (WHERE IsWorkDay = 'WeekEnd') - avg(day_sales) FILTER (WHERE IsWorkDay = 'WorkDay'))
         * 100.0 / nullif(avg(day_sales) FILTER (WHERE IsWorkDay = 'WorkDay'), 0) AS weekend_lift_pct
FROM daily
GROUP BY ProductCategoryName
ORDER BY weekend_lift_pct DESC NULLS LAST
-- @q100
-- @note ClickHouse quantile() is approximate (reservoir sampling); percentile_cont is exact, so the p90 thresholds and therefore the row set may differ slightly.
WITH cust AS (
  SELECT CustomerKey,
         count(DISTINCT SalesOrderNumber) AS orders,
         sum(SalesAmount) / nullif(count(DISTINCT SalesOrderNumber), 0) AS aov
  FROM retail.FactOnlineSales
  GROUP BY CustomerKey
),
thresholds AS (
  SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY orders) AS orders_p90,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY aov) AS aov_p90
  FROM cust
)
SELECT c.FirstName || ' ' || c.LastName AS customer_name,
       cu.orders,
       cu.aov,
       cu.orders * cu.aov AS lifetime_value
FROM cust cu
CROSS JOIN thresholds t
JOIN retail.DimCustomer c ON c.CustomerKey = cu.CustomerKey
WHERE cu.orders >= t.orders_p90 AND cu.aov >= t.aov_p90
ORDER BY lifetime_value DESC NULLS LAST
LIMIT 30
