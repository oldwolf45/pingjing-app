-- 冻结的 v1 结构，只给升级演练用。**不要跟着 schema.sql 改**：
-- 它的价值就在于「停在旧样子」，改一次演练就失效了。
-- 与当前 schema 的差别：没有 quotes 表，pool_states 没有 last_judged_at。

-- 紫苏叶策略 APP · 数据库结构 v1（冻结副本）
-- 约定：
--   金额 → 整数分（*_cents）；成本价 → 整数毫（cost_milli），移动加权是除法，浮点会累积误差
--   日期 → TEXT 'YYYY-MM-DD'；时间戳 → TEXT ISO8601 带 +08:00
--   外键 → ON DELETE RESTRICT，业务数据不允许级联删
--   只追加表 → BEFORE UPDATE/DELETE 触发器 RAISE(ABORT)

PRAGMA foreign_keys = ON;

-- ============================================================
-- 一、元数据
-- ============================================================

CREATE TABLE IF NOT EXISTS app_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 审计：所有写操作留痕。只追加。
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL DEFAULT 'app',
    action      TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    detail      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 二、股票
-- ============================================================

CREATE TABLE IF NOT EXISTS stocks (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    market      TEXT,                       -- sh / sz / bj
    status      TEXT NOT NULL DEFAULT 'active',  -- active / delisted
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS stock_profiles (
    code               TEXT PRIMARY KEY REFERENCES stocks(code) ON DELETE RESTRICT,
    sector             TEXT,
    industry           TEXT,
    main_business      TEXT,                -- JSON: [{"item":..,"pct":..}]
    tags               TEXT,                -- JSON 数组
    core_logic         TEXT,
    upgrade_conditions TEXT,
    downgrade_warning  TEXT,
    -- 操作参数：文本原样 + 解析值双存（"72元" 这种自由文本不能直接转数字）
    ideal_buy_text     TEXT,
    ideal_buy_price    REAL,
    stop_loss_text     TEXT,
    stop_loss_price    REAL,
    target_text        TEXT,
    target_price       REAL,
    position_suggest_raw REAL,              -- 原始建议仓位（可能是 15）
    position_suggest_cap REAL,              -- 裁剪后（10 上限）
    position_capped    INTEGER NOT NULL DEFAULT 0,
    notes              TEXT,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 三、池子
-- ============================================================

CREATE TABLE IF NOT EXISTS pool_states (
    code                    TEXT PRIMARY KEY REFERENCES stocks(code) ON DELETE RESTRICT,
    pool                    TEXT NOT NULL,          -- candidate/observation/reserve/tuna/removed
    weeks_in_pool           INTEGER NOT NULL DEFAULT 0,
    entry_week              TEXT,
    entry_score             REAL,
    price_ready             INTEGER NOT NULL DEFAULT 1,
    consecutive_weeks_above INTEGER NOT NULL DEFAULT 0,
    consecutive_weeks_below INTEGER NOT NULL DEFAULT 0,
    tuna_weeks              INTEGER NOT NULL DEFAULT 0,
    tuna_reason             TEXT,
    tuna_hits               TEXT,                   -- JSON 数组
    tuna_status             TEXT,
    exit_date               TEXT,
    exit_snapshot           TEXT,                   -- JSON，出池时固化，永不改
    back_weeks              INTEGER NOT NULL DEFAULT 0,
    back_miss               TEXT,                   -- JSON 数组
    remove_reason           TEXT,
    updated_at              TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pool_states_pool ON pool_states(pool);

-- 池子变动事件。只追加。
CREATE TABLE IF NOT EXISTS pool_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    name        TEXT,
    from_pool   TEXT NOT NULL,
    to_pool     TEXT NOT NULL,
    reason      TEXT,
    change_date TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pool_events_date ON pool_events(change_date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_pool_events_code ON pool_events(code);

-- ============================================================
-- 四、评分
-- ============================================================

CREATE TABLE IF NOT EXISTS score_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'weekly',  -- weekly / manual / migration
    status       TEXT NOT NULL DEFAULT 'running', -- running/ok/partial/failed
    algo_version TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    total        INTEGER NOT NULL DEFAULT 0,
    ok_count     INTEGER NOT NULL DEFAULT 0,
    fail_count   INTEGER NOT NULL DEFAULT 0,
    summary      TEXT
);

CREATE INDEX IF NOT EXISTS idx_score_runs_started ON score_runs(started_at DESC);

-- 评分快照。只追加。同一天重跑不覆盖，读时用 v_score_daily 取 id 最大者。
CREATE TABLE IF NOT EXISTS score_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    code                  TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    as_of_date            TEXT NOT NULL,
    track_barrier         REAL,
    earnings_growth       REAL,
    profitability         REAL,
    prosperity_check      REAL,
    heat_position         REAL,
    market_cap_elasticity REAL,
    total                 REAL NOT NULL,
    pool                  TEXT,
    total_mv_yi           REAL,
    financial             TEXT,      -- JSON
    barrier_detail        TEXT,      -- JSON
    notes                 TEXT,      -- JSON 数组
    source                TEXT NOT NULL DEFAULT 'live',  -- live/migration/history/migration_cache
    algo_version          TEXT,
    run_id                TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_snap_code_date ON score_snapshots(code, as_of_date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_snap_date ON score_snapshots(as_of_date DESC);

CREATE TABLE IF NOT EXISTS barrier_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    as_of_date   TEXT NOT NULL,
    score        REAL NOT NULL,
    confidence   INTEGER,
    dimensions   TEXT,      -- JSON
    source       TEXT NOT NULL,   -- auto / manual_override / fallback_manual
    needs_review INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_barrier_code_date ON barrier_snapshots(code, as_of_date DESC, id DESC);

-- 每票最新一条评分
CREATE VIEW IF NOT EXISTS v_score_current AS
SELECT s.*
  FROM score_snapshots s
  JOIN (
        SELECT code, MAX(as_of_date) AS d
          FROM score_snapshots
         GROUP BY code
       ) m ON m.code = s.code AND m.d = s.as_of_date
 WHERE s.id = (
        SELECT MAX(x.id) FROM score_snapshots x
         WHERE x.code = s.code AND x.as_of_date = s.as_of_date
       );

-- 同日重跑取最后一条
CREATE VIEW IF NOT EXISTS v_score_daily AS
SELECT s.*
  FROM score_snapshots s
 WHERE s.id = (
        SELECT MAX(x.id) FROM score_snapshots x
         WHERE x.code = s.code AND x.as_of_date = s.as_of_date
       );

-- ============================================================
-- 五、财务与情报
-- ============================================================

CREATE TABLE IF NOT EXISTS financial_periods (
    code           TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    period         TEXT NOT NULL,          -- 2025Q3 / 2025A
    report_date    TEXT,
    revenue        REAL,
    revenue_yoy    REAL,
    net_profit     REAL,
    net_profit_yoy REAL,
    deduct_profit  REAL,
    deduct_yoy     REAL,
    gross_margin   REAL,
    net_margin     REAL,
    roe            REAL,
    ocf            REAL,
    ocf_to_ni      REAL,
    source         TEXT,
    raw            TEXT,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (code, period)
);

CREATE TABLE IF NOT EXISTS financial_manual (
    code         TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    period       TEXT NOT NULL,
    field        TEXT NOT NULL,
    value        REAL,
    note         TEXT,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (code, period, field)
);

CREATE TABLE IF NOT EXISTS sector_intel (
    sector               TEXT PRIMARY KEY,
    cr3                  REAL,
    players_count        REAL,
    substitution_rate    REAL,
    substitution_urgency TEXT,
    cert_cycle_months    REAL,
    cert_level           TEXT,
    market_size_bn       REAL,
    market_growth        REAL,
    policy_level         TEXT,
    note                 TEXT,
    raw                  TEXT,
    updated_at           TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS trading_calendar (
    date    TEXT PRIMARY KEY,
    is_open INTEGER NOT NULL DEFAULT 1
);

-- ============================================================
-- 六、推荐（快照与跟踪只追加）
-- ============================================================

CREATE TABLE IF NOT EXISTS recommendations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    name          TEXT,
    as_of_date    TEXT NOT NULL,
    pool          TEXT,
    total_score   REAL,
    price_cents   INTEGER,
    thesis        TEXT,
    snapshot      TEXT,       -- JSON，当期完整评分
    judge_version TEXT NOT NULL DEFAULT 'v1-45d-hs300',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (code, as_of_date, judge_version)
);

CREATE TABLE IF NOT EXISTS recommendation_tracks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id         INTEGER NOT NULL REFERENCES recommendations(id) ON DELETE RESTRICT,
    as_of_date     TEXT NOT NULL,
    price_cents    INTEGER,
    pct_change     REAL,
    benchmark_pct  REAL,
    trading_days   INTEGER,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (rec_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS recommendation_evaluations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id        INTEGER NOT NULL REFERENCES recommendations(id) ON DELETE RESTRICT,
    tier          TEXT NOT NULL,   -- hard_miss / flat / lag_benchmark
    verdict       TEXT NOT NULL,   -- hit / miss / pending
    detail        TEXT,
    judge_version TEXT NOT NULL DEFAULT 'v1-45d-hs300',
    evaluated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (rec_id, tier, judge_version)
);

-- ============================================================
-- 七、持仓
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolios (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    cash_cents INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 交易流水。只追加：作废不是 UPDATE，而是插一条 side='VOID' 反向记录。
CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id   INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
    code           TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    side           TEXT NOT NULL CHECK (side IN ('BUY','SELL','VOID')),
    shares         INTEGER NOT NULL,
    price_cents    INTEGER NOT NULL,
    fee_cents      INTEGER NOT NULL DEFAULT 0,
    trade_date     TEXT NOT NULL,
    note           TEXT,
    voids_trade_id INTEGER,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_trades_portfolio ON trades(portfolio_id, trade_date, id);

CREATE TABLE IF NOT EXISTS decision_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL REFERENCES stocks(code) ON DELETE RESTRICT,
    week       TEXT NOT NULL,
    decision   TEXT NOT NULL,   -- add / hold / trim / watch
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (code, week)
);

-- ============================================================
-- 八、运维
-- ============================================================

CREATE TABLE IF NOT EXISTS data_sources (
    name        TEXT PRIMARY KEY,
    capability  TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 100,
    enabled     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'unknown',  -- ok/degraded/down/disabled/unknown
    last_ok_at  TEXT,
    last_error  TEXT,
    fail_count  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS source_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    capability TEXT NOT NULL,
    code       TEXT,
    url        TEXT,
    ok         INTEGER NOT NULL,
    http_code  INTEGER,
    latency_ms INTEGER,
    error      TEXT,
    called_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_source_calls_at ON source_calls(called_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',  -- running/ok/partial/failed/cancelled
    stage       TEXT,
    progress    INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    result      TEXT,
    started_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT
);

-- ============================================================
-- 九、只追加表锁死
-- ============================================================

CREATE TRIGGER IF NOT EXISTS trg_score_snapshots_no_update
BEFORE UPDATE ON score_snapshots
BEGIN
    SELECT RAISE(ABORT, 'score_snapshots 是只追加表，不允许 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_score_snapshots_no_delete
BEFORE DELETE ON score_snapshots
BEGIN
    SELECT RAISE(ABORT, 'score_snapshots 是只追加表，不允许 DELETE');
END;

CREATE TRIGGER IF NOT EXISTS trg_pool_events_no_update
BEFORE UPDATE ON pool_events
BEGIN
    SELECT RAISE(ABORT, 'pool_events 是只追加表，不允许 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_pool_events_no_delete
BEFORE DELETE ON pool_events
BEGIN
    SELECT RAISE(ABORT, 'pool_events 是只追加表，不允许 DELETE');
END;

CREATE TRIGGER IF NOT EXISTS trg_recommendations_no_update
BEFORE UPDATE ON recommendations
BEGIN
    SELECT RAISE(ABORT, 'recommendations 是只追加表，不允许 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_recommendations_no_delete
BEFORE DELETE ON recommendations
BEGIN
    SELECT RAISE(ABORT, 'recommendations 是只追加表，不允许 DELETE');
END;

CREATE TRIGGER IF NOT EXISTS trg_rec_tracks_no_update
BEFORE UPDATE ON recommendation_tracks
BEGIN
    SELECT RAISE(ABORT, 'recommendation_tracks 是只追加表，不允许 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_rec_tracks_no_delete
BEFORE DELETE ON recommendation_tracks
BEGIN
    SELECT RAISE(ABORT, 'recommendation_tracks 是只追加表，不允许 DELETE');
END;

CREATE TRIGGER IF NOT EXISTS trg_trades_no_update
BEFORE UPDATE ON trades
BEGIN
    SELECT RAISE(ABORT, 'trades 是只追加表，作废请插入 side=VOID 记录');
END;

CREATE TRIGGER IF NOT EXISTS trg_trades_no_delete
BEFORE DELETE ON trades
BEGIN
    SELECT RAISE(ABORT, 'trades 是只追加表，不允许 DELETE');
END;

CREATE TRIGGER IF NOT EXISTS trg_source_calls_no_update
BEFORE UPDATE ON source_calls
BEGIN
    SELECT RAISE(ABORT, 'source_calls 是只追加表，不允许 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trg_source_calls_no_delete
BEFORE DELETE ON source_calls
BEGIN
    SELECT RAISE(ABORT, 'source_calls 是只追加表，不允许 DELETE');
END;
