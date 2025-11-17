-- 创建 permissions 表
CREATE TABLE IF NOT EXISTS permissions (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    display_name VARCHAR(256) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 为 name 建索引（若需要额外快速查找）
CREATE UNIQUE INDEX IF NOT EXISTS idx_permissions_name ON permissions (name);

-- 触发器函数：在更新时设置 updated_at 为当前时间
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 为 permissions 表添加触发器：在每次 UPDATE 前执行
DROP TRIGGER IF EXISTS set_updated_at_on_permissions ON permissions;
CREATE TRIGGER set_updated_at_on_permissions
BEFORE UPDATE ON permissions
FOR EACH ROW
EXECUTE PROCEDURE trigger_set_updated_at();

-- 角色-权限关联表（role_permissions）
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE INDEX IF NOT EXISTS idx_role_permissions_role_id ON role_permissions (role_id);
CREATE INDEX IF NOT EXISTS idx_role_permissions_permission_id ON role_permissions (permission_id);
