# AcetiBot

食醋配方优化与知识问答项目。

## 结构

- `frontend/`：Vite + React 前端
- `api.py`：FastAPI 后端
- `data/standards/`：公开演示知识库资料
- `ingest.py`：部署时自动重建向量库
- `scripts/release.sh`：本地一键发布脚本

## 本地运行

后端：

```bash
pip install -r requirements.txt
python ingest.py
uvicorn api:app --host 127.0.0.1 --port 8013
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 最佳免费部署方案

推荐：

- 前端用 `Vercel`
- 后端用 `Render`

原因：

- `Vercel` 很适合托管 Vite 前端，访问快，和 GitHub 联动顺滑
- `Render` 更适合跑你这个 Python API，因为它需要 `uvicorn`、`ingest.py`、本地向量库重建
- 纯 `Vercel` 不适合当前后端结构，因为你的后端不是简单静态站点或轻量 serverless function

不推荐当前项目直接全站只上 `Vercel`。

## 首次部署

### 1. 部署后端到 Render

1. 登录 Render
2. 选择 `New +` -> `Blueprint`
3. 连接 GitHub 仓库 `AcetiBot`
4. Render 会读取根目录下的 `render.yaml`
5. 在控制台填入环境变量 `SILICONFLOW_API_KEY`
6. 等待后端部署完成

部署完成后你会拿到一个后端地址，例如：

```bash
https://acetibot-api.onrender.com
```

### 2. 部署前端到 Vercel

1. 登录 Vercel
2. 选择 `Add New...` -> `Project`
3. 导入 GitHub 仓库 `AcetiBot`
4. `Root Directory` 选择 `frontend`
5. 在环境变量里添加：

```bash
VITE_API_URL=https://你的-render-后端地址
```

6. 点击部署

`frontend/vercel.json` 已经处理了 SPA 路由刷新问题。

## 以后怎么一键发布

首次把 Vercel 和 Render 连接好以后，后续每次发版只需要：

```bash
cd /Users/jackieren/Smart_Pei
./scripts/release.sh "feat: 你的更新说明"
```

这个脚本会自动：

- 检查 Python 文件语法
- 构建前端
- `git add .`
- 提交 commit
- 推送到 `origin/main`

推送完成后：

- `Vercel` 会自动部署前端
- `Render` 会自动部署后端

## 哪些内容不会上传

默认不会上传：

- `.env`
- `chroma_db/`
- `frontend/node_modules/`
- `frontend/dist/`
- `data/consumer/`
- `data/papers/`
- `data/patents/`
- `data/sensor/`

当前仓库保留的是：

- 代码
- 配置
- 可公开的 `data/standards/` 演示资料

## 说明

- 免费版 `Render` 后端会休眠，首次唤醒可能会慢一点
- 这个方案很适合比赛演示、作品集展示、内测访问
- 如果以后要正式商用，再升级到持续在线的后端方案
