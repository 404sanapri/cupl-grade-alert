# 法大成绩提醒（CUPL Grade Alert）

教务处成绩是**一科一科**出的。这个项目定时读取你的成绩，**只在出现新科目成绩时**推一条消息到钉钉 / 飞书群，不用反复刷教务系统。

- 运行在你的 GitHub Actions 里：电脑关机也照常检查
- 凭据只放在你自己的仓库 Secrets，不进代码、不进日志
- 快照只存哈希键，不存分数，可一键清空

> 默认每 10 分钟检查一次。GitHub 的定时任务有排队延迟，实际时效约 5～15 分钟，不是秒级。

## 效果

群里会收到这样一条消息：

```
### 新增 1 门成绩

- 民法学原理四：亲属法与继承法

检测时间：2026-11-06 16:46
```

推送**只包含科目名**，不带分数、学分、绩点。具体成绩请在界面的「我的成绩」里查看。

首次运行只建立基线，**不会**把历史成绩一次性刷屏。

## 图形界面（不想碰命令行就用这个）

双击项目根目录的 **`start_ui.bat`**，浏览器会自动打开 `http://127.0.0.1:8765`。

界面分三块：

| 页面 | 能做什么 |
| --- | --- |
| **账号与通知** | 填学号、统一认证密码、钉钉/飞书 Webhook；保存配置；测试登录；发测试消息 |
| **已出科目** | 只列出本学期已出成绩的科目名，不含分数、绩点、学分；自动识别当前学期 |
| **监控** | 一键启动本机定时检查（5/10/15/30 分钟）；看运行记录与新增科目；清空基线 |

说明：

- 服务只监听 `127.0.0.1`，不对外网开放；
- 凭据写入 `config/.env`（已在 `.gitignore` 里），明文仅存于本机；
- 本机监控依赖这个窗口开着，关掉就停；要 24 小时运行请用 GitHub Actions；
- 也可以在终端启动：`python web\server.py`（加 `--port 8790` 换端口）。

## 四步开始（模板仓库用法）

### 1. 拿到仓库

点击右上角 **Use this template → Create a new repository**（或直接 Fork），仓库名随意。
建议保持 **Public**：Actions 免费且额度不限；Private 仓库每月 2000 分钟，10 分钟一次的频率也够用，但余量更紧。

### 2. 建一个群机器人

**钉钉**：群设置 → 机器人 → 添加机器人 → 自定义 → 安全设置勾选 **加签**，复制 `Webhook` 和 `SEC` 开头的密钥。

**飞书**：群设置 → 群机器人 → 添加 → 自定义机器人 → 安全设置勾选 **签名校验**，复制 Webhook 和密钥。

> 也可以改用「自定义关键词」，关键词填 `成绩` —— 本项目所有消息都包含「成绩」二字。
> **不要用 IP 白名单**：GitHub Actions 的出口 IP 每天都在变。

### 3. 填 5 个 Secret

仓库 **Settings → Secrets and variables → Actions → New repository secret**：

| 名称 | 必填 | 说明 |
| --- | --- | --- |
| `CUPL_USERNAME` | 是 | 学号 |
| `CUPL_PASSWORD` | 是 | **智慧法大统一认证**的密码 |
| `NOTIFY_CHANNEL` | 是 | `dingtalk` 或 `feishu` |
| `NOTIFY_WEBHOOK` | 是 | 机器人的 Webhook 地址 |
| `NOTIFY_SECRET` | 否 | 加签密钥；用关键词校验时留空 |

### 4. 启用并测试

1. 打开 **Actions** 标签页，若提示 workflow 未启用，点 **I understand my workflows, go ahead and enable them**。
2. 左侧选 **成绩更新监控 → Run workflow**，勾选 `dry_run` 先跑一次：只验证登录与解析，不推送、不写快照。
3. 日志出现 `解析到 N 条成绩` 即说明账密与接口正常。
4. 再跑一次（不勾 dry_run）：建立基线，之后每 10 分钟自动检查。

## 本机运行

```powershell
pip install -r requirements.txt
Copy-Item config\.env.example config\.env   # 填入账密与 Webhook
python src\monitor.py --dry-run             # 只验证登录与解析
python src\monitor.py                       # 真正执行一次
python src\monitor.py --test-notify         # 测通知渠道
python src\monitor.py --reset-state         # 清空快照
```

`config\.env` 已被 `.gitignore` 忽略；也可以用 `scripts\local_setup.ps1` 生成。

## 零凭据公告层（可选，适合分享给同学）

如果只想收到「教务处发布了成绩相关公告」的提醒，**完全不需要账号密码**：

```powershell
python src\announcement.py --dry-run   # 先看抓到什么
python src\announcement.py             # 正式执行
```

多群广播通过 Secret `ANNOUNCE_SUBSCRIBERS` 配置（JSON 数组）：

```json
[
  {"channel": "dingtalk", "url": "https://oapi.dingtalk.com/robot/send?access_token=...", "secret": "SEC..."},
  {"channel": "feishu", "url": "https://open.feishu.cn/open-apis/bot/v2/hook/...", "secret": ""}
]
```

关键词可用 `ANNOUNCE_KEYWORDS` 覆盖，默认 `成绩,分数`。

配套工作流 `announcement-watch.yml` 每 30 分钟跑一次。它的抓取量与订阅人数无关（永远只抓一次首页），所以是「广泛传播」场景下最省资源、也最安全的入口——代价是只能提示公告，不能精确到个人科目。

## 工作原理

```
Actions 定时触发
  → 统一认证 tpass：POST /tpass/rsa 取公钥 → RSA 加密账密 → 登录
  → POST /eams/teach/grade/course/person!historyCourseGrade.action?projectType=MAJOR
  → 按表头解析课程名 / 学分 / 总评成绩 / 绩点 / 学期
  → 与 state/grades.json 的哈希键集合比对
  → 有新增：推送消息；无新增：静默
  → 快照回写仓库（state/ 目录）
```

几个实现上的关键点，排查问题时有用：

- 教务系统的本地登录入口（`/eams/localLogin.action`）**不接受统一认证密码**，且对毫秒级提交返回「请不要过快点击」，所以本项目直接走统一认证，不用本地登录。
- 统一认证的密码不是明文提交，而是页面脚本用 RSA 公钥加密后放进 `ul` / `pl` 字段；这里用标准库 `pow()` 复现，不需要浏览器。
- 判定成败只能看页面文本（HTTP 状态码恒为 200）：`用户名或密码错误` / `请不要过快点击` / 出现验证码，分别对应不同处理。
- 「所有学期成绩」是 AJAX 局部刷新，真实接口就是上面那个 `historyCourseGrade.action`。

## 隐私与安全

- 账密只存在于 GitHub Secrets（加密存储）或本机 `.env`（已 gitignore）。
- 程序与日志**不会**打印密码、Cookie、学号或具体分数；推送消息只含课程名与检测时间。
- 快照只保存「学期 + 课程名」的 SHA-1 前 16 位，不含明文课程信息。
- 只查询你自己的成绩，不代查他人。
- 想彻底清除数据：`python src/monitor.py --reset-state`，并在 GitHub 上删除对应 Secrets。

**请尽快修改密码。** 如果你的密码仍然等于学号，建议先去 [智慧法大](https://ca.cupl.edu.cn) 改掉再配置本项目。

## 目录

```
src/            config 配置 / tpass_auth 统一认证 / eams_client 会话 / grade 解析 / state 快照 / notify 通知 / monitor 入口
scripts/        probe_interface.py 接口探测 / connectivity_check.py 连通性自检 / local_setup.ps1 本机初始化
tests/          31 项单元测试（python -m unittest discover -s tests）
docs/           方案对比 / 接口探测报告 / 配置向导 / 合规与隐私 / 故障排查
.github/workflows/  grade-check.yml 主监控 / connectivity-check.yml 连通性自检
```

## 时效与限制

| 项目 | 实际情况 |
| --- | --- |
| 检查频率 | 每 10 分钟（cron 最小 5 分钟） |
| 实际延迟 | GitHub 排队普遍 5～15 分钟，高峰期更久 |
| 额度 | Public 仓库免费不限量；Private 每月 2000 分钟 |
| 验证码 | 目前登录不触发；若出现会发告警并要求人工处理，不做自动打码 |
| 页面改版 | 解析失败会发「监控失效」告警，而不是静默失效 |

更多见 [docs/故障排查与回退.md](docs/故障排查与回退.md)。

## 免责声明

本项目仅用于查询**本人**成绩并提醒自己。请遵守中国政法大学信息系统使用规定，不要用于代查他人成绩、批量爬取或任何商业用途。因使用不当造成的后果由使用者自行承担。
