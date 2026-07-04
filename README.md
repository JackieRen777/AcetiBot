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

## 推荐部署方案

当前项目统一采用云服务器部署：

- 前端静态文件交给 `nginx`
- 后端用 `uvicorn + systemd`
- 向量库保存在服务器本地磁盘

这样做的好处：

- 不受免费平台休眠影响
- 更适合当前 `FastAPI + ingest.py + chroma_db` 结构
- 便于和现有项目隔离，例如单独使用 `127.0.0.1:8013` 与公网 `8080`

## 阿里云部署

当前推荐的隔离部署方式如下：

- 项目目录：`/www/wwwroot/AcetiBot`
- 后端监听：`127.0.0.1:8013`
- 前端入口：`nginx` 对外暴露 `8080`
- 旧项目保持在原有 `80/443` 和既有后端端口，不直接改动

仓库中已经附带：

- `deploy/acetibot.service`
- `deploy/acetibot_8080.conf`
- `scripts/deploy_aliyun.sh`

首次部署建议流程：

1. 在服务器创建目录 `/www/wwwroot/AcetiBot`
2. 将本地 `.env` 放到服务器该目录下
3. 安装 Python 依赖并创建 `venv`
4. 执行 `python ingest.py` 构建 `chroma_db`
5. 安装 `deploy/acetibot.service` 到 `/etc/systemd/system/`
6. 安装 `deploy/acetibot_8080.conf` 到 `/etc/nginx/conf.d/`
7. 执行：

```bash
systemctl daemon-reload
systemctl enable --now acetibot
nginx -t
systemctl reload nginx
```

8. 在阿里云安全组中放行 `8080/tcp`

## 以后怎么一键发布

如果已经完成阿里云首发部署，后续本地更新可以直接执行：

```bash
cd /Users/jackieren/Smart_Pei
./scripts/deploy_aliyun.sh
```

这个脚本会自动：

- 检查 Python 文件语法
- 用 `VITE_API_URL=/api` 构建前端
- 同步代码到服务器
- 重新执行 `ingest.py`
- 重启 `acetibot` 服务

如果你还希望同步推送 GitHub，可以继续使用：

```bash
./scripts/release.sh "feat: 你的更新说明"
```

## 哪些内容不会上传

默认不会上传：

- `.env`
- `chroma_db/`
- `frontend/node_modules/`
- `frontend/dist/`
- `data/consumer/`
- `data/papers/`
- `data/patents/`
- `data/flavor/`

当前仓库保留的是：

- 代码
- 配置
- 可公开的 `data/standards/` 演示资料

## 说明

- 如果公网无法访问 `http://你的公网IP:8080`，优先检查阿里云安全组是否已放行 `8080/tcp`
- `.env` 不应提交到 GitHub
- 如果 API Key 曾在截图或日志中暴露，建议尽快轮换
