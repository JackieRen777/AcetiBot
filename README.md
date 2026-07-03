# Smart_Pei

食醋配方优化与知识问答项目，包含：

- `frontend/`：Vite + React 前端
- `api.py`：FastAPI 后端
- `data/standards/`：公开演示知识库资料
- `ingest.py`：部署时自动重建向量库

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

## 推送到 GitHub

项目当前还没有绑定远程仓库。可以按下面步骤推送：

```bash
cd /Users/jackieren/Smart_Pei
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<你的仓库名>.git
git push -u origin main
```

说明：

- `.env`、`chroma_db/`、`frontend/node_modules/` 不会上传
- 当前保留 `data/standards/` 作为公开演示资料，其余数据目录默认忽略

## 免费外网部署

推荐使用 Render 的 Blueprint，一次部署前后端：

1. 把仓库推到 GitHub
2. 登录 Render
3. 选择 `New +` -> `Blueprint`
4. 连接这个 GitHub 仓库
5. Render 会读取根目录下的 `render.yaml`
6. 在控制台填写 `SILICONFLOW_API_KEY`
7. 等待部署完成

部署后会得到两个服务：

- `acetibot-api`：FastAPI 接口
- `acetibot-web`：React 网站

## 部署说明

- 后端部署时会自动执行 `python ingest.py`，用仓库内的 `data/standards/` 重建知识库
- 前端会自动读取 Render 分配给后端的外网域名
- 免费套餐适合演示和比赛，不适合正式高并发生产环境
