/*
 Navicat Premium Dump SQL

 Source Server         : 控制台数据库
 Source Server Type    : PostgreSQL
 Source Server Version : 180000 (180000)
 Source Host           : localhost:5432
 Source Catalog        : console_local_db
 Source Schema         : public

 Target Server Type    : PostgreSQL
 Target Server Version : 180000 (180000)
 File Encoding         : 65001

 Date: 05/12/2025 18:01:04
*/


-- ----------------------------
-- Type structure for permission_type
-- ----------------------------
DROP TYPE IF EXISTS "public"."permission_type";
CREATE TYPE "public"."permission_type" AS ENUM (
  'menu',
  'button',
  'api'
);
ALTER TYPE "public"."permission_type" OWNER TO "postgres";

-- ----------------------------
-- Type structure for user_status
-- ----------------------------
DROP TYPE IF EXISTS "public"."user_status";
CREATE TYPE "public"."user_status" AS ENUM (
  'pending',
  'active',
  'suspended',
  'banned'
);
ALTER TYPE "public"."user_status" OWNER TO "postgres";

-- ----------------------------
-- Sequence structure for permissions_id_seq
-- ----------------------------
DROP SEQUENCE IF EXISTS "public"."permissions_id_seq";
CREATE SEQUENCE "public"."permissions_id_seq" 
INCREMENT 1
MINVALUE  1
MAXVALUE 2147483647
START 1
CACHE 1;
ALTER SEQUENCE "public"."permissions_id_seq" OWNER TO "postgres";

-- ----------------------------
-- Sequence structure for roles_id_seq
-- ----------------------------
DROP SEQUENCE IF EXISTS "public"."roles_id_seq";
CREATE SEQUENCE "public"."roles_id_seq" 
INCREMENT 1
MINVALUE  1
MAXVALUE 2147483647
START 1
CACHE 1;
ALTER SEQUENCE "public"."roles_id_seq" OWNER TO "postgres";

-- ----------------------------
-- Sequence structure for token_blacklist_id_seq
-- ----------------------------
DROP SEQUENCE IF EXISTS "public"."token_blacklist_id_seq";
CREATE SEQUENCE "public"."token_blacklist_id_seq" 
INCREMENT 1
MINVALUE  1
MAXVALUE 2147483647
START 1
CACHE 1;
ALTER SEQUENCE "public"."token_blacklist_id_seq" OWNER TO "postgres";

-- ----------------------------
-- Sequence structure for users_id_seq
-- ----------------------------
DROP SEQUENCE IF EXISTS "public"."users_id_seq";
CREATE SEQUENCE "public"."users_id_seq" 
INCREMENT 1
MINVALUE  1
MAXVALUE 2147483647
START 1
CACHE 1;
ALTER SEQUENCE "public"."users_id_seq" OWNER TO "postgres";

-- ----------------------------
-- Table structure for permissions
-- ----------------------------
DROP TABLE IF EXISTS "public"."permissions";
CREATE TABLE "public"."permissions" (
  "id" int4 NOT NULL DEFAULT nextval('permissions_id_seq'::regclass),
  "name" varchar(128) COLLATE "pg_catalog"."default" NOT NULL,
  "display_name" varchar(256) COLLATE "pg_catalog"."default" NOT NULL,
  "description" text COLLATE "pg_catalog"."default",
  "created_at" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_at" timestamptz(6) NOT NULL DEFAULT now(),
  "is_deletable" bool NOT NULL DEFAULT false,
  "is_editable" bool NOT NULL DEFAULT false
)
;
ALTER TABLE "public"."permissions" OWNER TO "postgres";
COMMENT ON COLUMN "public"."permissions"."is_deletable" IS '权限是否可以被删除，默认不可删除';
COMMENT ON COLUMN "public"."permissions"."is_editable" IS '权限是否可以被编辑，默认不可编辑';

-- ----------------------------
-- Records of permissions
-- ----------------------------
BEGIN;
INSERT INTO "public"."permissions" ("id", "name", "display_name", "description", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (2, 'api:user:*', '请求用户接口', '获取用户信息的权限', '2025-11-17 03:25:48.167232+00', '2025-11-19 07:06:37.576849+00', 'f', 'f');
INSERT INTO "public"."permissions" ("id", "name", "display_name", "description", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (1, 'api:*', '请求所有接口', '允许用户请求所有接口', '2025-11-17 02:05:47.496069+00', '2025-11-19 07:06:48.082221+00', 'f', 'f');
INSERT INTO "public"."permissions" ("id", "name", "display_name", "description", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (3, 'api:role:*', '访问角色接口', '允许访问所有角色接口', '2025-11-19 07:07:14.929565+00', '2025-11-19 07:07:14.929565+00', 'f', 'f');
INSERT INTO "public"."permissions" ("id", "name", "display_name", "description", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (4, 'api:persmission:*', '访问权限接口', '允许访问所有权限接口', '2025-11-19 07:07:31.94566+00', '2025-11-19 07:07:31.94566+00', 'f', 'f');
COMMIT;

-- ----------------------------
-- Table structure for role_permissions
-- ----------------------------
DROP TABLE IF EXISTS "public"."role_permissions";
CREATE TABLE "public"."role_permissions" (
  "role_id" int4 NOT NULL,
  "permission_id" int4 NOT NULL
)
;
ALTER TABLE "public"."role_permissions" OWNER TO "postgres";

-- ----------------------------
-- Records of role_permissions
-- ----------------------------
BEGIN;
INSERT INTO "public"."role_permissions" ("role_id", "permission_id") VALUES (11, 1);
INSERT INTO "public"."role_permissions" ("role_id", "permission_id") VALUES (12, 2);
INSERT INTO "public"."role_permissions" ("role_id", "permission_id") VALUES (13, 3);
INSERT INTO "public"."role_permissions" ("role_id", "permission_id") VALUES (14, 4);
COMMIT;

-- ----------------------------
-- Table structure for roles
-- ----------------------------
DROP TABLE IF EXISTS "public"."roles";
CREATE TABLE "public"."roles" (
  "id" int4 NOT NULL DEFAULT nextval('roles_id_seq'::regclass),
  "name" varchar(64) COLLATE "pg_catalog"."default" NOT NULL,
  "display_name" varchar(128) COLLATE "pg_catalog"."default" NOT NULL,
  "description" text COLLATE "pg_catalog"."default",
  "is_active" bool NOT NULL DEFAULT true,
  "created_at" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_at" timestamptz(6) NOT NULL DEFAULT now(),
  "is_deletable" bool NOT NULL DEFAULT false,
  "is_editable" bool NOT NULL DEFAULT false
)
;
ALTER TABLE "public"."roles" OWNER TO "postgres";

-- ----------------------------
-- Records of roles
-- ----------------------------
BEGIN;
INSERT INTO "public"."roles" ("id", "name", "display_name", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (11, 'admin', '管理员', '管理员用户，拥有所有的权限。', 't', '2025-11-14 06:43:13.51664+00', '2025-11-14 06:43:13.51664+00', 'f', 'f');
INSERT INTO "public"."roles" ("id", "name", "display_name", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (12, 'user_admin', '用户管理员', '用户管理员', 't', '2025-11-19 07:03:14.294867+00', '2025-11-19 07:03:14.294867+00', 'f', 'f');
INSERT INTO "public"."roles" ("id", "name", "display_name", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (13, 'role_admin', '角色管理员', '角色管理员', 't', '2025-11-19 07:03:29.203128+00', '2025-11-19 07:03:29.203128+00', 'f', 'f');
INSERT INTO "public"."roles" ("id", "name", "display_name", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (14, 'permission_admin', '权限管理员', '权限管理员', 't', '2025-11-19 07:03:44.043309+00', '2025-11-19 07:03:44.043309+00', 'f', 'f');
INSERT INTO "public"."roles" ("id", "name", "display_name", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (10, 'normal', '普通用户', '普通用户，只有基本的访问权限。', 't', '2025-11-14 06:43:13.51664+00', '2025-11-14 06:43:13.51664+00', 'f', 'f');
COMMIT;

-- ----------------------------
-- Table structure for token_blacklist
-- ----------------------------
DROP TABLE IF EXISTS "public"."token_blacklist";
CREATE TABLE "public"."token_blacklist" (
  "id" int4 NOT NULL DEFAULT nextval('token_blacklist_id_seq'::regclass),
  "token_hash" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "expires_at" timestamptz(6) NOT NULL,
  "created_at" timestamptz(6) NOT NULL
)
;
ALTER TABLE "public"."token_blacklist" OWNER TO "postgres";

-- ----------------------------
-- Records of token_blacklist
-- ----------------------------
BEGIN;
COMMIT;

-- ----------------------------
-- Table structure for user_roles
-- ----------------------------
DROP TABLE IF EXISTS "public"."user_roles";
CREATE TABLE "public"."user_roles" (
  "user_id" int4 NOT NULL,
  "role_id" int4 NOT NULL
)
;
ALTER TABLE "public"."user_roles" OWNER TO "postgres";

-- ----------------------------
-- Records of user_roles
-- ----------------------------
BEGIN;
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (47, 10);
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (41, 11);
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (43, 12);
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (44, 13);
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (45, 14);
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (46, 12);
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (46, 13);
INSERT INTO "public"."user_roles" ("user_id", "role_id") VALUES (46, 14);
COMMIT;

-- ----------------------------
-- Table structure for users
-- ----------------------------
DROP TABLE IF EXISTS "public"."users";
CREATE TABLE "public"."users" (
  "id" int4 NOT NULL DEFAULT nextval('users_id_seq'::regclass),
  "name" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "email" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "password" varchar(100) COLLATE "pg_catalog"."default",
  "description" varchar(128) COLLATE "pg_catalog"."default",
  "is_active" bool NOT NULL DEFAULT true,
  "created_at" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_at" timestamptz(6) NOT NULL DEFAULT now(),
  "is_deletable" bool NOT NULL DEFAULT false,
  "is_editable" bool NOT NULL DEFAULT false
)
;
ALTER TABLE "public"."users" OWNER TO "postgres";

-- ----------------------------
-- Records of users
-- ----------------------------
BEGIN;
INSERT INTO "public"."users" ("id", "name", "email", "password", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (44, '角色管理员', 'role@example.com', '$2b$12$oThXg9Jwt3Ea8LbkHIUKt.H/4npjGDwYg2l5e0eCN8GtRVBaFrGn2', NULL, 't', '2025-11-26 01:05:46.794926+00', '2025-11-26 01:05:46.794926+00', 'f', 'f');
INSERT INTO "public"."users" ("id", "name", "email", "password", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (45, '权限管理员', 'permission@example.com', '$2b$12$aOojd8b7Lpz0tWFoWpb7wec6YYdlmDfSwMePmGVjDbYVEDBBxdGaG', NULL, 't', '2025-11-26 01:06:03.44389+00', '2025-11-26 01:06:03.44389+00', 'f', 'f');
INSERT INTO "public"."users" ("id", "name", "email", "password", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (46, 'rbac管理员', 'rbac@example.com', '$2b$12$xgRUPf/UN0yuqBSTXE6dQuYpTB8ALG9vODzwYluLZNBa17mlkhzFG', NULL, 't', '2025-11-26 01:06:16.975121+00', '2025-11-26 01:06:16.975121+00', 'f', 'f');
INSERT INTO "public"."users" ("id", "name", "email", "password", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (43, '用户管理员', 'user@example.com', '$2b$12$P3ZFrwEgliY9jvsv82LjC./VF4yFAAM04kAKdzyJGVVn8mSpwhk2u', NULL, 't', '2025-11-25 09:30:29.643522+00', '2025-11-25 09:30:29.643522+00', 'f', 'f');
INSERT INTO "public"."users" ("id", "name", "email", "password", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (47, '普通用户', 'normal@example.com', '$2b$12$bq3DGj26kC4W5vsDDZ5qLOszTGfVSZzNL019ujq2cUYxxoJ1FufXG', NULL, 't', '2025-11-26 01:08:40.491969+00', '2025-11-26 01:08:40.491969+00', 'f', 'f');
INSERT INTO "public"."users" ("id", "name", "email", "password", "description", "is_active", "created_at", "updated_at", "is_deletable", "is_editable") VALUES (41, '管理员', 'admin@example.com', '$2b$12$FTri.LHs.2oC.PxrUwZ7z.QfQjfhv/kYPtnMsZf/ouqo25b7GWbXi', '是管理员', 't', '2025-11-14 06:43:13.51664+00', '2025-11-25 07:18:33.115819+00', 'f', 'f');
COMMIT;

-- ----------------------------
-- Function structure for trigger_set_updated_at
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."trigger_set_updated_at"();
CREATE FUNCTION "public"."trigger_set_updated_at"()
  RETURNS "pg_catalog"."trigger" AS $BODY$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$BODY$
  LANGUAGE plpgsql VOLATILE
  COST 100;
ALTER FUNCTION "public"."trigger_set_updated_at"() OWNER TO "postgres";

-- ----------------------------
-- Alter sequences owned by
-- ----------------------------
ALTER SEQUENCE "public"."permissions_id_seq"
OWNED BY "public"."permissions"."id";
SELECT setval('"public"."permissions_id_seq"', 4, true);

-- ----------------------------
-- Alter sequences owned by
-- ----------------------------
ALTER SEQUENCE "public"."roles_id_seq"
OWNED BY "public"."roles"."id";
SELECT setval('"public"."roles_id_seq"', 15, true);

-- ----------------------------
-- Alter sequences owned by
-- ----------------------------
ALTER SEQUENCE "public"."token_blacklist_id_seq"
OWNED BY "public"."token_blacklist"."id";
SELECT setval('"public"."token_blacklist_id_seq"', 32, true);

-- ----------------------------
-- Alter sequences owned by
-- ----------------------------
ALTER SEQUENCE "public"."users_id_seq"
OWNED BY "public"."users"."id";
SELECT setval('"public"."users_id_seq"', 47, true);

-- ----------------------------
-- Indexes structure for table permissions
-- ----------------------------
CREATE UNIQUE INDEX "idx_permissions_name" ON "public"."permissions" USING btree (
  "name" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);

-- ----------------------------
-- Triggers structure for table permissions
-- ----------------------------
CREATE TRIGGER "set_updated_at_on_permissions" BEFORE UPDATE ON "public"."permissions"
FOR EACH ROW
EXECUTE PROCEDURE "public"."trigger_set_updated_at"();

-- ----------------------------
-- Uniques structure for table permissions
-- ----------------------------
ALTER TABLE "public"."permissions" ADD CONSTRAINT "permissions_name_key" UNIQUE ("name");

-- ----------------------------
-- Primary Key structure for table permissions
-- ----------------------------
ALTER TABLE "public"."permissions" ADD CONSTRAINT "permissions_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table role_permissions
-- ----------------------------
CREATE INDEX "idx_role_permissions_permission_id" ON "public"."role_permissions" USING btree (
  "permission_id" "pg_catalog"."int4_ops" ASC NULLS LAST
);
CREATE INDEX "idx_role_permissions_role_id" ON "public"."role_permissions" USING btree (
  "role_id" "pg_catalog"."int4_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table role_permissions
-- ----------------------------
ALTER TABLE "public"."role_permissions" ADD CONSTRAINT "role_permissions_pkey" PRIMARY KEY ("role_id", "permission_id");

-- ----------------------------
-- Indexes structure for table roles
-- ----------------------------
CREATE INDEX "idx_roles_display_name" ON "public"."roles" USING btree (
  "display_name" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "idx_roles_is_active" ON "public"."roles" USING btree (
  "is_active" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "idx_roles_name" ON "public"."roles" USING btree (
  "name" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);

-- ----------------------------
-- Uniques structure for table roles
-- ----------------------------
ALTER TABLE "public"."roles" ADD CONSTRAINT "roles_name_key" UNIQUE ("name");

-- ----------------------------
-- Primary Key structure for table roles
-- ----------------------------
ALTER TABLE "public"."roles" ADD CONSTRAINT "roles_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table token_blacklist
-- ----------------------------
CREATE INDEX "ix_token_blacklist_expires_at" ON "public"."token_blacklist" USING btree (
  "expires_at" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);
CREATE INDEX "ix_token_blacklist_id" ON "public"."token_blacklist" USING btree (
  "id" "pg_catalog"."int4_ops" ASC NULLS LAST
);
CREATE UNIQUE INDEX "ix_token_blacklist_token_hash" ON "public"."token_blacklist" USING btree (
  "token_hash" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table token_blacklist
-- ----------------------------
ALTER TABLE "public"."token_blacklist" ADD CONSTRAINT "token_blacklist_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table user_roles
-- ----------------------------
CREATE INDEX "idx_user_roles_role_id_copy1" ON "public"."user_roles" USING btree (
  "role_id" "pg_catalog"."int4_ops" ASC NULLS LAST
);
CREATE INDEX "idx_user_roles_user_id_copy1" ON "public"."user_roles" USING btree (
  "user_id" "pg_catalog"."int4_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table user_roles
-- ----------------------------
ALTER TABLE "public"."user_roles" ADD CONSTRAINT "user_roles_copy1_pkey" PRIMARY KEY ("user_id", "role_id");

-- ----------------------------
-- Indexes structure for table users
-- ----------------------------
CREATE INDEX "ix_users_created_at" ON "public"."users" USING btree (
  "created_at" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);
CREATE INDEX "ix_users_description" ON "public"."users" USING btree (
  "description" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE UNIQUE INDEX "ix_users_email" ON "public"."users" USING btree (
  "email" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_users_id" ON "public"."users" USING btree (
  "id" "pg_catalog"."int4_ops" ASC NULLS LAST
);
CREATE INDEX "ix_users_is_active" ON "public"."users" USING btree (
  "is_active" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "ix_users_name" ON "public"."users" USING btree (
  "name" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_users_updated_at" ON "public"."users" USING btree (
  "updated_at" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table users
-- ----------------------------
ALTER TABLE "public"."users" ADD CONSTRAINT "users_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Foreign Keys structure for table role_permissions
-- ----------------------------
ALTER TABLE "public"."role_permissions" ADD CONSTRAINT "role_permissions_permission_id_fkey" FOREIGN KEY ("permission_id") REFERENCES "public"."permissions" ("id") ON DELETE CASCADE ON UPDATE NO ACTION;
ALTER TABLE "public"."role_permissions" ADD CONSTRAINT "role_permissions_role_id_fkey" FOREIGN KEY ("role_id") REFERENCES "public"."roles" ("id") ON DELETE CASCADE ON UPDATE NO ACTION;

-- ----------------------------
-- Foreign Keys structure for table user_roles
-- ----------------------------
ALTER TABLE "public"."user_roles" ADD CONSTRAINT "user_roles_copy1_role_id_fkey" FOREIGN KEY ("role_id") REFERENCES "public"."roles" ("id") ON DELETE CASCADE ON UPDATE NO ACTION;
ALTER TABLE "public"."user_roles" ADD CONSTRAINT "user_roles_copy1_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users" ("id") ON DELETE CASCADE ON UPDATE NO ACTION;
