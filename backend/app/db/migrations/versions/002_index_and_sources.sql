-- 紫苏叶策略 APP · 数据库迁移 v002
-- 版本：2
-- 名称：002_index_and_sources
-- 说明：评分来源索引 + 数据源初始登记
-- 新增内容：
--   - idx_snap_source 索引
--   - data_sources 表的初始种子数据

CREATE INDEX IF NOT EXISTS idx_snap_source ON score_snapshots(source);

INSERT OR IGNORE INTO data_sources(name, capability, priority, enabled)
VALUES
    ('tencent_quote',   'quote',     10, 1),
    ('tencent_kline',   'kline',     10, 1),
    ('eastmoney_fin',   'financial', 10, 1),
    ('eastmoney_report','report',    10, 1),
    ('eastmoney_flow',  'flow',      20, 1),
    ('sina_quote',      'quote',     90, 0);
