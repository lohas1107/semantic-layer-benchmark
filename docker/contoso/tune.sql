-- Planner/safety settings baked into both image variants.
-- Run with psql -v schema=<retail|dbo> after the data is loaded.
--
-- Expression statistics: without them PostgreSQL estimates
-- `EXTRACT(YEAR FROM DateKey) = 2009` at ~0.2% of rows instead of ~1/3 and picks
-- nested loops (q88 went from >120 s to ~3 s). Covered spellings: EXTRACT with
-- and without ::int, date_part and date_trunc, on every fact table's DateKey.

SELECT format(
  'CREATE STATISTICS IF NOT EXISTS %1$I.%2$I ON (EXTRACT(%3$s FROM %4$I)::int) FROM %1$I.%5$I;'
  'CREATE STATISTICS IF NOT EXISTS %1$I.%6$I ON (EXTRACT(%3$s FROM %4$I)) FROM %1$I.%5$I;'
  'CREATE STATISTICS IF NOT EXISTS %1$I.%7$I ON (date_part(%8$L, %4$I)) FROM %1$I.%5$I;'
  'CREATE STATISTICS IF NOT EXISTS %1$I.%9$I ON (date_trunc(%8$L, %4$I)) FROM %1$I.%5$I;',
  c.table_schema, 'st_' || c.table_name || '_' || f || '_int', f, c.column_name, c.table_name,
  'st_' || c.table_name || '_' || f, 'st_' || c.table_name || '_' || f || '_dp', lower(f),
  'st_' || c.table_name || '_' || f || '_tr')
FROM information_schema.columns c
CROSS JOIN (VALUES ('YEAR'), ('QUARTER'), ('MONTH')) AS parts(f)
WHERE c.table_schema = :'schema' AND c.table_name LIKE 'fact%' AND c.column_name = 'datekey'
\gexec

ANALYZE;

-- JIT compilation costs seconds per query on this workload and never pays off.
ALTER DATABASE contoso SET jit = off;
-- A runaway self-join (e.g. q33) must not fill the disk.
ALTER DATABASE contoso SET temp_file_limit = '4GB';
