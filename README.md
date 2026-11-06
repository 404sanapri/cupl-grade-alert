# 法大成绩提醒（CUPL Grade Alert）

教务处成绩是**一科一科**出的。这个项目定时读取你的成绩，**只在出现新科目成绩时**推一条消息到钉钉 / 飞书群，不用反复刷教务系统。

- 运行在**你自己的电脑**上（Windows 计划任务）：每 10 分钟检查一次
- 凭据只放在本机 `config\.env`（已 gitignore），不进代码、不进日志
- 快照只存哈希键，不存分数，可一键清空

> ⚠️ **为什么不用 GitHub Actions 定时**：教务系统 `jwxt.cupl.edu.cn:80` 对境外 IP 直接超时。
> 2026-11-06 实测：同一轮里 `tpass:443` 与 RSA 公钥接口都 PASS，只有 `jwxt:80` 是 `ConnectTimeout`
> —— 是目标侧拦截，不是 runner 断网。所以 `grade-check.yml` 的 `cron` 已注释，
> 定时改由本机计划任务驱动，详见 [docs/本机计划任务.md](docs/本机计划任务.md)。

## 效果

群里会收到这样一条消息：

```
### 新增 1 门成绩

- 民法学原理四：亲属法与继承法

检测时间：2026-11-06 16:46
```

推送**只包含科目名**，不带分数、学分、绩点。具体成绩请在界面的「我的成绩」里查看。

首次运行只建立基线，**不会**把历史成绩一次性刷屏。

## 桌面版 exe（最省事：打开就监测，关掉就停）

双击 `法大成绩提醒.exe` → 立即检查一次 → 之后每 N 分钟自动检查；**关掉窗口就彻底停止**。
首次运行会弹出设置窗口，填学号、统一认证密码、机器人 Webhook 即可。

自己打包只要一条命令（产物在 `dist\`，约 18 MB，单文件自带 Python）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

细节、排错、与计划任务的区别见 [docs/桌面版.md](docs/桌面版.md)。

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
- 界面里的「监控」开关依赖这个窗口开着，关掉就停；**想让它一直跑，用下面的计划任务**；
- 也可以在终端启动：`python web\server.py`（加 `--port 8790` 换端口）。

## 三步开始（本机计划任务）

### 1. 装依赖、写配置

```powershell
pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_setup.ps1
```

`local_setup.ps1` 会问你学号、**智慧法大统一认证**密码、机器人 Webhook，写入 `config\.env`（已 gitignore）。
不想敲命令行就双击 `start_ui.bat`，在界面「账号与通知」里填，效果一样。

### 2. 建一个群机器人

**钉钉**：群设置 → 机器人 → 添加机器人 → 自定义 → 安全设置勾选 **加签**，复制 `Webhook` 和 `SEC` 开头的密钥。

**飞书**：群设置 → 群机器人 → 添加 → 自定义机器人 → 安全设置勾选 **签名校验**，复制 Webhook 和密钥。

> 也可以改用「自定义关键词」，关键词填 `成绩` —— 本项目所有消息都包含「成绩」二字。
> **不要用 IP 白名单**：家庭宽带的公网 IP 也会变。

### 3. 注册计划任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action install -IntervalMinutes 10
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local_task.ps1 -Action run   # 立刻跑一次，建立基线
```

第一次运行只建立基线，不会把历史成绩刷屏；之后每 10 分钟检查一次。
`-Action status` 看运行状态，`-Action remove` 卸载。细节见 [docs/本机计划任务.md](docs/本机计划任务.md)。

> 计划任务只在你**登录 Windows 后**运行；关机 / 休眠期间不检查，开机后会补跑一次。

## 手工运行

```powershell
Copy-Item config\.env.example config\.env   # 填入账密与 Webhook
python src\monitor.py --dry-run             # 只验证登录与解析
python src\monitor.py                       # 真正执行一次
python src\monitor.py --test-notify         # 测通知渠道
python src\monitor.py --reset-state         # 清空快照
python src\scheduled_run.py                 # 无界面跑一次，并写入运行记录（计划任务用的就是它）
python scripts\connectivity_check.py        # 自检：本机出口能否访问教务系统
```

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
Windows 计划任务（每 10 分钟）
  → scripts\run_monitor.ps1 选 Python、写日志
  → src\scheduled_run.py 跑一次并追加 logs\runs.jsonl
  → 统一认证 tpass：POST /tpass/rsa 取公钥 → RSA 加密账密 → 登录
  → POST /eams/teach/grade/course/person!historyCourseGrade.action?projectType=MAJOR
  → 按表头解析课程名 / 学分 / 总评成绩 / 绩点 / 学期
  → 与 state/grades.json 的哈希键集合比对
  → 有新增：推送消息；无新增：静默
  → 快照落盘（state/ 目录，本机文件）
```

几个实现上的关键点，排查问题时有用：

- 教务系统的本地登录入口（`/eams/localLogin.action`）**不接受统一认证密码**，且对毫秒级提交返回「请不要过快点击」，所以本项目直接走统一认证，不用本地登录。
- 统一认证的密码不是明文提交，而是页面脚本用 RSA 公钥加密后放进 `ul` / `pl` 字段；这里用标准库 `pow()` 复现，不需要浏览器。
- 判定成败只能看页面文本（HTTP 状态码恒为 200）：`用户名或密码错误` / `请不要过快点击` / 出现验证码，分别对应不同处理。
- 「所有学期成绩」是 AJAX 局部刷新，真实接口就是上面那个 `historyCourseGrade.action`。

## 隐私与安全

- 账密只存在于本机 `config\.env`（已 gitignore），不上传任何服务器。
- 程序与日志**不会**打印密码、Cookie、学号或具体分数；推送消息只含课程名与检测时间。
- 快照只保存「学期 + 课程名」的 SHA-1 前 16 位，不含明文课程信息。
- 只查询你自己的成绩，不代查他人。
- 想彻底清除数据：`python src/monitor.py --reset-state`，再删掉 `config\.env` 与 `logs\`。

**请尽快修改密码。** 如果你的密码仍然等于学号，建议先去 [智慧法大](https://ca.cupl.edu.cn) 改掉再配置本项目。

## 目录

```
app.py          桌面版入口（tkinter 窗口，打开即监测、关掉即停）
src/            config 配置 / tpass_auth 统一认证 / eams_client 会话 / grade 解析 / state 快照 / notify 通知
                monitor 手工入口 / scheduled_run 计划任务入口 / runlog 运行记录
scripts/        probe_interface.py 接口探测 / connectivity_check.py 连通性自检
                local_setup.ps1 写 config\.env / local_task.ps1 计划任务管理 / run_monitor.ps1 计划任务包装
                build_exe.ps1 打包 exe
tests/          单元测试（python -m unittest discover -s tests）
docs/           方案对比 / 接口探测报告 / 配置向导 / 合规与隐私 / 故障排查 / 本机计划任务 / 桌面版
.github/workflows/  grade-check.yml 成绩监控（定时已停用）/ announcement-watch.yml 公告广播 / connectivity-check.yml 连通性自检
```

## 时效与限制

| 项目 | 实际情况 |
| --- | --- |
| 检查频率 | 每 10 分钟（脚本允许 5～120 分钟，别设太快） |
| 实际延迟 | 基本准时；关机 / 休眠期间不检查，开机补跑一次 |
| 运行前提 | 电脑开着、并且当前用户已登录 Windows |
| 验证码 | 目前登录不触发；若出现会发告警并要求人工处理，不做自动打码 |
| 页面改版 | 解析失败会发「监控失效」告警，而不是静默失效 |
| GitHub Actions | 只保留公告广播与手动触发；境外 runner 访问不了 `jwxt:80` |

更多见 [docs/故障排查与回退.md](docs/故障排查与回退.md)。

## 免责声明

本项目仅用于查询**本人**成绩并提醒自己。请遵守中国政法大学信息系统使用规定，不要用于代查他人成绩、批量爬取或任何商业用途。因使用不当造成的后果由使用者自行承担。
