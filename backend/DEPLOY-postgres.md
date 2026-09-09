# 多设备部署：切换到 PostgreSQL

接「主板多机」前，把数据库从 SQLite 换成 Postgres——SQLite 是单写者，多台手机同时心跳/领任务/传截图会争锁（`database is locked`）。代码已做好兼容，切换只需配置。

## 为什么
- **并发写**：Postgres 支持真正的多写并发；多 worker 下用 `SELECT … FOR UPDATE SKIP LOCKED` 领任务（代码已按方言自动启用），不会重复下发同一条任务。
- SQLite 现已开启 WAL + busy_timeout（见 `app/db/session.py`），可撑 2~3 台过渡，但不是多机方案。

## 步骤
1. 起一个 Postgres（本机/局域网/云均可），建库建用户：
   ```sql
   CREATE DATABASE douyin_agent;
   CREATE USER agent WITH PASSWORD '强密码';
   GRANT ALL PRIVILEGES ON DATABASE douyin_agent TO agent;
   ```
2. 装驱动：
   ```bash
   pip install "psycopg[binary]>=3.2"
   ```
3. 在 `backend/.env` 设置（仅此一处需要改）：
   ```
   DATABASE_URL=postgresql+psycopg://agent:强密码@192.168.1.50:5432/douyin_agent
   ```
4. 首次启动会 `create_all` 建好全部表（含所有字段）。**Postgres 不跑 SQLite 的 ad-hoc 列迁移**——以后改表结构请用 Alembic 正式迁移。
5. 多 worker 起服务（CPU 核数相应调整）：
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8010 --workers 4
   ```
   领任务用行级锁，多 worker 安全。

## 手机端
每台手机的 Agent App 后端地址填**服务器局域网 IP**（如 `http://192.168.1.50:8010/api/v1`），不要填 `127.0.0.1`（那是 USB 调试用的）。手机与服务器需在同一局域网。

## 数据迁移（可选）
现有 SQLite 数据要带过去：用 `pgloader sqlite://./data/agent.db postgresql://…`，或导出内容/账号后重新录入（量小时更省事）。
