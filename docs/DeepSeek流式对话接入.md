# DeepSeek 流式对话

React `/chat` 页面通过当前登录用户的访问令牌调用 Python 后端
`POST /api/v1/chat/stream`，后端再请求 DeepSeek。
DeepSeek API Key 仅保存在后端环境配置中，浏览器只收到对话事件。

## 环境配置

在选定的环境文件（开发 `.env.dev`，预发 `.env.pre`，生产 `.env.prod`）中设置：

```dotenv
DEEPSEEK_API_KEY=填写你的新密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_MAX_TOKENS=4096
DEEPSEEK_READ_TIMEOUT_SECONDS=60
CHAT_STREAM_TIMEOUT_SECONDS=300
```

开发配置已经写入本地 `.env.dev`，该文件被 Git 忽略且权限为 `600`。
会话中公开过的密钥建议撤销，再将新密钥填入相应环境文件。
系统环境变量优先于环境文件；Docker 启动器也会传入这些变量。
配置的模型必须是账号可用的模型 ID。当前默认使用非思考模式，输出预算由服务端限制。
缺少 API Key 时只会使对话接口返回 503，其他页面可以正常使用。

安装后端依赖并启动开发环境：

```bash
cd console_server
uv sync
bash scripts/start.sh dev
```

另一个终端启动前端，Vite 已将 `/api` 代理到本地 8000 端口：

```bash
cd react-console-ui
pnpm install
pnpm dev
```

## 数据库与授权

项目统一使用空库初始化，执行 `sql/init.sql`，其中已包含 AI 对话角色和权限。
用连接目标库的数据库工具执行，
或在后端目录运行（环境变量为 `psql` 支持的 PostgreSQL 连接地址）：

```bash
psql "$CONSOLE_PSQL_URL" -v ON_ERROR_STOP=1 -f sql/init.sql
```

`sql/init.sql` 包含重建语句，仅用于首次初始化空库，应用重启和后续发布无需重复导入。
初始化时创建以下角色和权限，不自动给账号分配角色：

| 项目 | 编码 | 用途 |
| --- | --- | --- |
| 独立角色 | `ai_user` | AI 对话用户 |
| 接口权限 | `api:chat:use` | 使用流式接口及页面 |
| 菜单权限 | `menu:chat` | 由接口权限推导可见性 |

使用管理员账号，在「用户管理 → 设置角色」给目标用户分配「AI 对话用户」。
保留原有基础角色即可。授权成功后前端会刷新权限，菜单中显示「AI 对话」。
角色名称本身不授予权限，其他角色也可通过显式分配 `api:chat:use` 获得对话能力。
现有全局管理员的 `api:*` 继续包含此接口；普通用户和各模块维护管理员不会自动获得。

接口在每次请求时校验真实 JWT、有效登录会话、用户状态、启用角色和权限。
撤销角色、停用角色或退出会话会阻止后续请求。
前端页面权限变化或卸载、退出登录、点击停止生成时会中止正在进行的请求。
后台修改授权不会单独推送取消现有连接，前端沿用现有权限同步周期。

## 请求与事件

请求需携带当前用户的 `Authorization: Bearer <access_token>`。
消息只接受用户和助手角色，交替排列，最后一条必须为用户问题。

```json
{
  "messages": [
    {"role": "user", "content": "请介绍一下这个系统"}
  ]
}
```

响应为 `text/event-stream`，心跳以 SSE 注释发送。

```text
event: delta
data: {"content":"你好"}

event: done
data: {"finish_reason":"stop","usage":{"total_tokens":12}}

```

`delta` 追加文本，`done` 表示完整结束。
中途失败发送 `event: error` 和 `{"message":"生成中断，请重新发送"}`，不会再次发送 `done`。
未收到完整 `done` 的断流被前端视为失败；错误发生后不会自动重试生成。
连接前的错误使用 HTTP 状态：401 未登录、403 无对话权限、422 消息不合法、
429 上游繁忙、503 未配置或上游凭证/额度不可用、502 上游异常、504 连接超时。
上游认证错误不会作为用户登录过期处理。

页面支持多轮消息、Markdown、停止生成和新建对话。
对话只保留在当前页面内存中，刷新或离开页面后清空，不写入数据库或浏览器存储。
下一次提问携带最近最多 20 个完整轮次，受单条 16000 字符、总计 64000 字符限制。
失败、停止或过长的轮次不加入后续上下文。API 最多接受 100 条消息。
模型输出使用 `@ant-design/x-markdown`，原始 HTML 转义为文本，并经过 DOMPurify 净化。
支持流式语法补全、代码高亮与复制、KaTeX 公式；完成、停止或失败时结束该回复的流式渲染。

## 反向代理

生产环境的反向代理需透传流式响应。对应 `/api/v1/chat/stream` 的 Nginx 配置例如：

```nginx
location = /api/v1/chat/stream {
    proxy_pass http://backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_cache off;
    gzip off;
    proxy_read_timeout 360s;
}
```

后端返回 `X-Accel-Buffering: no` 和 `Cache-Control: no-store`，并每 15 秒维持心跳。
根据实际后端地址及 `CHAT_STREAM_TIMEOUT_SECONDS` 调整代理配置。
生成前释放鉴权数据库连接，正常结束、失败和取消均关闭上游 HTTP 连接。

## 验证

后端回归使用隔离内存数据库和模拟 DeepSeek，真实验证 JWT、会话、角色、
分片、错误及取消。测试配置在命令执行时注入，不连接业务数据库：

```bash
ENV=test DATABASE_URL=postgresql+asyncpg://test:test@localhost/console_test \
SECRET_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

上面的签名密钥仅供隔离测试，部署密钥需随机生成。
未配置 `CONSOLE_TEST_DATABASE_URL` 的 PostgreSQL 专用测试会跳过。

前端验证：

```bash
pnpm test:chat
pnpm test:permissions
pnpm build
```
