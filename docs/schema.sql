-- ============================================================================
-- 演示项目数据库结构 —— SQLite 3.45+（Python 标准库 sqlite3 可直接执行）
--
-- 约定：
--   1. 主键统一使用 TEXT（ULID/UUID 字符串），便于分布式生成、跨表引用。
--   2. 时间字段统一使用 TEXT，UTC 的 ISO-8601 格式（如 2025-01-31T08:00:00.000Z），
--      可直接做字符串比较与排序。
--   3. 半结构化字段（工具参数、模型原始响应）用 TEXT 存 JSON，并加 json_valid() 约束。
--   4. 布尔值用 INTEGER 0/1，并加 CHECK 约束。
--   5. 外键写入时必须开启 PRAGMA foreign_keys = ON（见文件末尾），否则约束不生效。
--   6. 表名/字段名一律 snake_case，单数形式（users/agents 为约定俗成的复数表名例外）。
--
-- 建库示例：
--   sqlite3 demo.db < docs/schema.sql
-- 或在 Python 中：
--   conn = sqlite3.connect("demo.db"); conn.executescript(open("docs/schema.sql", encoding="utf-8").read())
-- ============================================================================

PRAGMA foreign_keys = ON;

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. users —— 使用系统的用户
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    email         TEXT UNIQUE,
    role          TEXT NOT NULL DEFAULT 'member'
                  CHECK (role IN ('admin', 'member', 'viewer')),
    password_hash TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(username) BETWEEN 3 AND 32),
    CHECK (email IS NULL OR email LIKE '%_@_%.__%')
);

-- ----------------------------------------------------------------------------
-- 2. agents —— 智能体（lead / teammate / subagent），对应 agent/ 目录下的实现
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    role          TEXT NOT NULL DEFAULT 'teammate'
                  CHECK (role IN ('lead', 'teammate', 'subagent')),
    model         TEXT NOT NULL,
    system_prompt TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(name)) > 0)
);

-- ----------------------------------------------------------------------------
-- 3. sessions —— 一次会话（对话上下文容器）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    agent_id       TEXT REFERENCES agents (id) ON DELETE SET NULL,
    title          TEXT NOT NULL DEFAULT 'Untitled session',
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'archived')),
    message_count  INTEGER NOT NULL DEFAULT 0 CHECK (message_count >= 0),
    token_count    INTEGER NOT NULL DEFAULT 0 CHECK (token_count >= 0),
    context_summary TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_active_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ----------------------------------------------------------------------------
-- 4. messages —— 会话内的消息（按 seq 单调递增排序）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    seq               INTEGER NOT NULL CHECK (seq > 0),
    role              TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content           TEXT NOT NULL DEFAULT '',
    token_count       INTEGER NOT NULL DEFAULT 0 CHECK (token_count >= 0),
    stop_reason       TEXT,
    parent_message_id TEXT REFERENCES messages (id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (session_id, seq)
);

-- ----------------------------------------------------------------------------
-- 5. tasks —— 持久化任务（对应 .tasks/ 目录下 JSON 记录的数据库版本）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id             TEXT PRIMARY KEY,
    session_id     TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    parent_task_id TEXT REFERENCES tasks (id) ON DELETE CASCADE,
    subject        TEXT NOT NULL CHECK (length(trim(subject)) > 0),
    description    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')),
    priority       INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 4), -- 1 = 最高
    owner_agent_id TEXT REFERENCES agents (id) ON DELETE SET NULL,
    created_by     TEXT REFERENCES users (id) ON DELETE SET NULL,
    attempt_count  INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    blocked_reason TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at     TEXT,
    completed_at   TEXT,
    CHECK (parent_task_id IS NULL OR parent_task_id <> id),
    CHECK (completed_at IS NULL OR status IN ('completed', 'failed', 'cancelled'))
);

-- ----------------------------------------------------------------------------
-- 6. task_dependencies —— 任务依赖（DAG：task_id 依赖 depends_on_task_id）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id            TEXT NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

-- ----------------------------------------------------------------------------
-- 7. tool_calls —— 工具调用审计（一次 assistant 消息可触发多次工具调用）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_calls (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    message_id    TEXT REFERENCES messages (id) ON DELETE CASCADE,
    task_id       TEXT REFERENCES tasks (id) ON DELETE SET NULL,
    tool_name     TEXT NOT NULL CHECK (length(trim(tool_name)) > 0),
    arguments     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(arguments)),
    result        TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'succeeded', 'failed', 'denied')),
    error_message TEXT,
    duration_ms   INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ----------------------------------------------------------------------------
-- 8. memory_notes —— 跨会话记忆（对应 .memory/ 目录下的 Markdown 笔记）
--    scope: session_id 为空表示全局记忆，否则为会话级记忆
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_notes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT REFERENCES users (id) ON DELETE CASCADE,
    session_id  TEXT REFERENCES sessions (id) ON DELETE CASCADE,
    type        TEXT NOT NULL DEFAULT 'project'
                CHECK (type IN ('user', 'project', 'reference', 'feedback')),
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL CHECK (length(content) > 0),
    pinned      INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ============================================================================
-- 索引 —— 按 API 的实际查询路径设计（见 docs/db-design.md 第 4 节）
-- ============================================================================

-- users：登录/查重（username、email 已由 UNIQUE 约束隐式建唯一索引）
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);

-- agents：按启用状态列出可选智能体
CREATE INDEX IF NOT EXISTS idx_agents_active_role ON agents (is_active, role);

-- sessions：某用户的会话列表（按最近活跃倒序）；某智能体进行中的会话
CREATE INDEX IF NOT EXISTS idx_sessions_user_active ON sessions (user_id, status, last_active_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_agent_status ON sessions (agent_id, status);

-- messages：UNIQUE(session_id, seq) 已覆盖“按会话顺序拉取消息”
CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages (session_id, role);

-- tasks：待办队列、按负责人、按会话、按父任务、按状态批量统计
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks (status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_status ON tasks (owner_agent_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks (parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created_by ON tasks (created_by, created_at DESC);

-- task_dependencies：主键已覆盖“查某任务的依赖”；反向查询“谁依赖我”需单独建索引
CREATE INDEX IF NOT EXISTS idx_task_deps_depends_on ON task_dependencies (depends_on_task_id);

-- tool_calls：按消息、按任务、按工具名做统计与排障
CREATE INDEX IF NOT EXISTS idx_tool_calls_message ON tool_calls (message_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task ON tool_calls (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_name_status ON tool_calls (tool_name, status, created_at DESC);

-- memory_notes：同名笔记在同一作用域内唯一；按作用域 + 更新时间检索
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_notes_scope
    ON memory_notes (IFNULL(user_id, ''), IFNULL(session_id, ''), type, name);
CREATE INDEX IF NOT EXISTS idx_memory_notes_session ON memory_notes (session_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_notes_pinned ON memory_notes (pinned, updated_at DESC);

COMMIT;

-- 结构版本号，供后续迁移脚本判断（PRAGMA user_version）
PRAGMA user_version = 1;
