-- 创建 permissions 表
CREATE TABLE IF NOT EXISTS permissions (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    display_name VARCHAR(256) NOT NULL,
    description TEXT,
    is_deletable BOOLEAN NOT NULL DEFAULT FALSE,
    is_editable BOOLEAN NOT NULL DEFAULT FALSE,
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

-- 给 permissions 表添加可删除和可编辑标志字段
-- 添加 is_deletable 字段，默认为 false（不可删除）
ALTER TABLE permissions 
ADD COLUMN IF NOT EXISTS is_deletable BOOLEAN NOT NULL DEFAULT FALSE;

-- 添加 is_editable 字段，默认为 false（不可编辑）
ALTER TABLE permissions 
ADD COLUMN IF NOT EXISTS is_editable BOOLEAN NOT NULL DEFAULT FALSE;

-- 为新字段添加注释
COMMENT ON COLUMN permissions.is_deletable IS '权限是否可以被删除，默认不可删除';
COMMENT ON COLUMN permissions.is_editable IS '权限是否可以被编辑，默认不可编辑';

-- 为新字段创建索引以提高查询性能
-- CREATE INDEX IF NOT EXISTS idx_permissions_is_deletable ON permissions (is_deletable);
-- CREATE INDEX IF NOT EXISTS idx_permissions_is_editable ON permissions (is_editable);

-- 如果需要设置某些系统权限为可编辑或可删除，可以在这里添加更新语句
-- 例如：
-- UPDATE permissions SET is_editable = TRUE WHERE name LIKE 'temp_%';
-- UPDATE permissions SET is_deletable = TRUE WHERE name LIKE 'custom_%';