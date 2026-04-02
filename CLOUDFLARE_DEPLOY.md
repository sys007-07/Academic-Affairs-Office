# Cloudflare 部署说明

## 当前项目结构

- `worker.py`: Cloudflare Workers 的入口
- `wrangler.toml`: Cloudflare 配置
- `jwc_core.py`: 公告抓取和解析逻辑
- `jwc_messages.html`: 前端页面
- `scrape_jwc.py`: 本地调试入口，Cloudflare 部署时不使用

## 在 Cloudflare Dashboard 中部署

1. 打开 `Workers & Pages`
2. 选择 `Create application`
3. 选择 `Import a repository`
4. 连接 GitHub，并选择当前仓库
5. 创建 Worker 时，名称请使用 `academic-affairs-office`

注意：
`wrangler.toml` 里的 `name` 也必须是 `academic-affairs-office`，否则 Cloudflare Workers Builds 会因为名称不匹配而失败。

## Build 配置

- Root directory: 留空
- Build command: 留空
- Deploy command: `npx wrangler deploy`

## 部署成功后的访问

- 首页: `/`
- 公告列表接口: `/api/notices`
- 文章详情接口: `/api/article?id=0`
- 健康检查: `/api/ping`

## 旧 Render 文件

按你的规则，我没有主动删除旧文件。

如果你确认以后不再使用 Render，请你自行删除旧的 Render 相关文件。

我这次整理时，当前工作目录里已经没有看到 `render.yaml` 了；如果你的仓库或别的目录里还保留着旧的 Render 配置，请你自行处理。
