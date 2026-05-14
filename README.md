# CxKitty-Web version

<br />

一个基于 Python + Flask 的学习通任务执行工作台，支持课程任务点自动处理、任务进度与日志实时推送，并提供“全局并发限制 + 用户级排队”的调度能力（支持跨设备查看排队/进度）。

## 目录

- 功能概览
- 运行环境
- 快速启动
- 配置说明（config.yml）
- 任务调度与并发模型（重点）
- Web 页面与接口
- 数据与文件结构
- 常见问题排查
- 安全与合规

## 功能概览

- 账号登录
  - 密码登录：`/api/login/passwd`
  - 二维码登录：`/api/login/qr/create` + `/api/login/qr/poll`
- 课程任务执行
  - 章节任务：视频/文档/章节测验（按配置启用）
  - 实时日志推送：Socket.IO `task_log`
  - 实时进度推送：Socket.IO `task_progress`
- 全局并发与排队（多用户）
  - 全局同时运行用户数限制（默认 60，可配置）
  - 超出并发后进入全局等待队列（FIFO）
  - 同一用户的课程任务串行执行：一个用户所有课程队列执行完，再切换到下一个用户
  - “运行中追加课程”会触发重新排队：当前课程结束后暂停该用户，按新时间插入队尾
- 跨设备同步（同账号多设备）
  - 当前选择的课程（未开始也能同步）
  - 排队状态与队列列表（不会因刷新/换设备丢失）

## 运行环境

- Python：3.10+（建议 3.10\~3.12）
- OS：Windows / Linux（均可）
- 依赖：见 `requirements.txt`

## 快速启动

### 1) 安装依赖

在项目根目录执行：

```bash
pip install -r requirements.txt
```

### 2) 准备配置文件

项目根目录已有 `config.yml`，按需修改：

- `server.max_concurrent_users`：全局并发上限
- `video/work/document`：任务执行参数
- `searchers`：题库/搜题后端（启用作业时必须配置）

### 3) 启动 Web 服务

```bash
python web/app.py
```

启动后访问：

- `http://127.0.0.1:5000/`（登录页）
- `http://127.0.0.1:5000/courses`（课程列表）
- `http://127.0.0.1:5000/tasks`（任务与日志）
- `http://127.0.0.1:5000/settings`（配置页）

## 配置说明（config.yml）

### server（服务器全局）

```yaml
server:
  max_concurrent_users: 60
```

- `max_concurrent_users`
  - 含义：全局最多允许同时“运行中”的用户数（按 `owner_id` 计数）
  - 达到上限后：仍允许访问与提交任务，但用户会进入全局等待队列，按进入时间顺序启动
  - 环境变量优先级更高：`MAX_CONCURRENT_USERS`

### video / work / document / exam

控制各类型任务点是否启用、等待时长、倍速、上报频率等，详见 `config.yml` 内注释。

### searchers

启用章节测验/考试自动答题时，需要至少配置一个搜索器，否则服务会提示配置不完整。

## 任务调度与并发模型（重点）

### 关键概念

- `owner_id`
  - 用户任务的归属键：优先使用登录后的 `puid`，否则回退到 `client_id`
  - Socket.IO 日志 room 也使用 `owner_id`
- “全局并发上限”
  - 同时允许运行的用户数上限（默认 60）
- “用户队列”
  - 每个用户拥有一个课程队列（SQLite 持久化）
  - 队列内任务按加入时间串行执行

### 执行策略（当前实现）

- 全局调度器按 FIFO 从全局等待队列取出下一个用户
- 为该用户启动 worker：
  - 循环取该用户 `pending` 课程任务，逐个执行
  - 每完成一个课程就落库标记完成
  - 直到该用户队列为空才算“该用户完成”，然后切换下一个用户
- 运行中追加课程：
  - 新课程会加入该用户的队列
  - 同时设置 `requeue_after_current=1`
  - 当前课程结束后，该用户会被强制重新排队（插队尾），以保证“追加课程打乱原顺序”

## Web 页面与接口

### 页面

- `/`：登录页
- `/courses`：课程列表（选择任务目标/加入队列）
- `/tasks`：任务进度、排队状态、执行日志
- `/settings`：配置编辑

### 主要接口（节选）

- 登录
  - `POST /api/login/passwd`
  - `POST /api/login/qr/create`
  - `POST /api/login/qr/poll`
- 课程与配置
  - `GET /api/courses`
  - `GET /api/config`
  - `POST /api/config`
- 任务选择（跨设备同步）
  - `POST /api/task/selection`：保存当前选择（未开始也同步）
  - `POST /api/task/selection/clear`：清空选择
- 队列与执行
  - `POST /api/task/queue/add`：加入用户队列（持久化）
  - `POST /api/task/queue/remove`：移除待执行项
  - `POST /api/task/queue/clear`：清空待执行队列
  - `POST /api/task/start`：将“当前选择课程”加入队列并开始排队/执行
  - `POST /api/task/queue/start`：启动队列（若在排队/运行则返回 queued/started）
  - `POST /api/task/stop`：停止当前用户任务并清空待执行
  - `GET /api/task/status`：查看运行/排队/队列/选择

## 数据与文件结构

关键目录：

- `web/`：Flask 服务端与静态资源
  - `web/app.py`：主服务入口与 API
  - `web/static/html/`：页面模板
  - `web/static/js/`：前端逻辑（任务页/课程页）
  - `web/task_store.py`：任务调度 SQLite 存储（`data/scheduler.db`）
- `cxapi/`：学习通接口封装
- `resolver/`：任务点执行器（视频/文档/答题）
- `data/`
  - `scheduler.db`：调度数据库（自动创建）
- `logs/`：运行日志

## 常见问题排查

### 1) 刷新/换设备后“课程选择/排队信息消失”

当前状态由 SQLite 持久化维护：

- 当前选择：`user_profile.selected_task_json`
- 队列：`user_tasks`
- 排队：`global_user_queue`

如果仍出现异常，优先确认：

- 服务是否已启动并正常连接到当前项目代码
- `data/scheduler.db` 是否可写（权限/磁盘）

### 2) 5000 端口 404/路由不一致

通常是多个旧进程同时占用 5000，导致请求打到不同进程。建议先确保只保留一个监听进程。

### 3) 多次点击开始导致卡死

前端已做启动锁，后端也做了同用户启动互斥；如果仍卡死，检查浏览器扩展/网络代理干扰。

## 安全与合规

- 不要把邮箱/授权码/Token 等敏感信息提交到公开仓库
- 本项目仅用于技术研究与学习，使用者需自行确保符合相关平台规则与法律法规

## 相关项目

- [ReposSamueli924/chaoxing: 超星学习通/超星尔雅/泛雅超星全自动无人值守完成任务点](https://github.com/ReposSamueli924/chaoxing)
- [RainySY/chaoxing-xuexitong-autoflush: 超星学习通全自动无人值守视频刷课程序，使用协议发包来实现。](https://github.com/RainySY/chaoxing-xuexitong-autoflush)
- [lyj0309/chaoxing-xuexitong-autoflush: 超星学习通全自动无人值守刷课程序，使用协议发包来实现，无需浏览器，支持自动过测验、过视频。](https://github.com/lyj0309/chaoxing-xuexitong-autoflush)
- [chettoy/FxxkStar: API and unofficial client for the SuperStar mooc platform | 超星学习通的API和非官方客户端脚本，为学生提供更好的学习体验](https://github.com/chettoy/FxxkStar)
- [ocsjs/ocsjs: OCS 网课助手，网课脚本，帮助大学生解决网课难题，目前支持网课：超星学习通，知道智慧树，支持脚本猫以及油猴脚本运行。](https://github.com/ocsjs/ocsjs)
- [SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy: 基于“学小易”搜题API的学习通答题/考试油猴脚本题库代理](https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy)
- [CodFrm/cxmooc-tools: 一个 超星(学习通)/智慧树(知到)/中国大学mooc 学习工具，火狐/谷歌/油猴支持，全自动任务/视频倍速秒过/作业考试题库/验证码自动打码](https://github.com/CodFrm/cxmooc-tools)
- [AlanStar233/CxKitty ](https://github.com/AlanStar233/CxKitty)**超星学习通答题姬**

## Disclaimers

- 本项目以 GPL-3.0 License 作为开源协议，这意味着你需要遵守相应的规则（详见 [LICENSE](LICENSE)）
- 本项目仅适用于学习研究，任何人不得以此用于盈利
- 使用本项目造成的任何后果与本人无关
