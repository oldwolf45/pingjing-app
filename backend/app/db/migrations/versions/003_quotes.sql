-- 紫苏叶策略 APP · 数据库迁移 v003
-- 版本：3
-- 名称：003_quotes
-- 说明：新增最新行情表 quotes
-- 为什么单独建表而不是塞进 stock_profiles：
--   行情是易变的、按日刷新；画像是一年改一次的东西。
--   混在一起会让人不敢动画像，也无法区分"行情旧了"和"画像没维护"。

CREATE TABLE IF NOT EXISTS quotes (
    code          TEXT PRIMARY KEY REFERENCES stocks(code) ON DELETE RESTRICT,
    price         REAL,
    prev_close    REAL,
    pct           REAL,
    open          REAL,
    high          REAL,
    low           REAL,
    turnover_pct  REAL,
    pe            REAL,
    total_mv_yi   REAL,
    float_mv_yi   REAL,
    quote_time    TEXT,
    quoted_at     TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'tencent',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
