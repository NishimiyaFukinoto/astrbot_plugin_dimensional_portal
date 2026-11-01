# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.4.0] - 2026-11-01

### 新增
- **管理控制台 V2**：安全策略开关（敏感界面允许截图 / 允许点击输入）+ 五个可调参数
  （每分钟操作上限、任务超时、离线判定、截图质量、单图上限），改完立刻生效，不用重载插件
- 新增 `GET /admin/audit?limit=&offset=` 审计分页接口，控制台里「操作审计」卡按批加载、新的在前；
  后端只读日志尾部 512 KB 并做 `(mtime, size)` 缓存
- `/admin/state` 多返回 `editable`（每个数字字段的合法区间由后端下发，前端不再硬编码）
- 审计里的 `change` 只记**真正改动**的字段（前端保存会提交整份策略，后端和改动前的值对比后再落审计）
- 控制台 4 张卡变 5 张：设备状态 / QQ 白名单 / 安全策略 / 操作审计 / 急停

### 修复
- 控制台轮询会把用户正在编辑的参数冲掉——`policyDirty` 或输入框聚焦时跳过回填
- 审计条目在窄屏被挤成很窄的一列——改成「头部 + 正文」两行结构，≤560px 单列
- `/admin/audit` 把查询串原样传给下游实现（拿到字符串去切片会 500），现在服务端转 int 并夹紧
- `tests/admin_console_test.py` 的临时目录改用普通 `mkdir`（`tempfile.mkdtemp` 在 Windows 上
  建的目录某些沙箱环境下写不进去，会让测试误报 500）

### 测试
- `tests/admin_console_test.py` 41 → **58 项**（新增安全策略开关、审计分页、越界/类型校验等用例）

## [0.3.0] - 2026-11-01

### 变更（**破坏性**：包名与仓库名改名）
- 项目更名为 **次元门 / Dimensional Portal**（原名「我的圣剑 / Excalibur」）
- 插件包名 `astrbot_plugin_excalibur` → **`astrbot_plugin_dimensional_portal`**
  （WebUI 里显示的中文名仍是「次元门」，路径不用中文以免报错）
- 手机脚本 `excalibur_agent.js` → **`dimensional_portal_agent.js`**
- 环境变量 `EXCALIBUR_*` → **`DIMENSIONAL_PORTAL_*`**
- 插件类 `ExcaliburPhoneBridge` → **`DimensionalPortalPhoneBridge`**
- healthz 的 `service` 字段 → `dimensional-portal-phone-bridge`

### 升级注意
- 老用户请先停插件 → 删除旧的 `astrbot_plugin_excalibur` 目录与配置 → 装新的
  `astrbot_plugin_dimensional_portal`（白名单等配置要重新填，或把旧配置改名后复用）

### 新增
- 管理控制台 V1：`admin_token` 留空时首次启动自动生成并写回配置（启动日志打印完整地址），
  手机浏览器走 Tailscale 也能打开 `/console`；能看设备状态、增删 QQ 白名单（chips 交互）、一键急停清队列
- 控制台接口：`/admin/state`、`/admin/config`（只改白名单，返回落盘回读值）、`/admin/stop`；
  鉴权 `X-Admin-Token` 或 `?k=`，错 k 页面 403 / 接口 401，同 IP 失败 10 次锁 60 秒（429）
- 白名单改动就地重建 `SafetyPolicy` 并写 `kind="admin"` 审计，不需要重载插件；`admin_token: "off"` 时四个路由完全不注册
- 控制台视觉 V1/V1.2：从深色夜空改成**明色调「樱花晴空」**——真实照片背景（Unsplash License，
  桌面 `bg-wide.jpg` 1600×1067 / 窄屏 `bg-tall.jpg` 1000×1333）+ 白色玻璃卡片 + 深玫瑰渐变标题；
  新增只读路由 `GET /console-assets/{name}`（只放行上面两个文件名），CSP 补 `img-src 'self'`
- `/console` 在本机浏览器打开且没带 `k` 时 **302 自动补上密钥**（`_is_loopback` 判 127.0.0.1/::1/::ffff:127.0.0.1），
  别的机器访问照旧 403，密钥不外泄
- 两套白名单语义分离：新增 `@filter.on_llm_request()` 钩子 `_hide_tools_for_outsiders`，
  不在「QQ 号白名单」里的人本次请求里拿不到任何 `phone_*` 工具——**照样能找 AI 聊天，只是不能遥控手机**

### 安全
- 敏感截图二次校验：截图后用手机回报的**真实前台包名 + UI 文字**再查一次，命中直接丢弃，
  不落临时文件、不发到 QQ（心跳可能落后 10 秒，只查心跳会漏）
- 新增 `allow_sensitive_write`（默认关）：敏感界面上的点击/长按/滑动/输入/按键/开应用/找字点击
  一律拒绝并写 `deny_sensitive_write` 审计；开着也仍然每秒最多 20 次
- `open_app` 用中文应用名打开后，按手机回报的真实包名复查黑名单（以前只查包名形态，
  「微信」能绕过）；落到黑名单会提示用 `phone_key home` 退出
- `/healthz` 不带 token 只回 `{ok, service}`，带正确 token 才暴露 online/queue/uptime
- 非 ASCII token（如 `?token=令牌`）不再让 `hmac.compare_digest` 抛 `TypeError` 变 500，按不匹配回 401
- `/result` 的 `ok` 必须是严格布尔；`"false"` / `0` / `null` 一律 400，不再 `bool()` 兜底当成功

### 修复
- 控制台白名单 chips 每 5 秒重绘一次（入场动画重播、看着像闪）→ 列表没变就不重建 DOM
- 控制台长页下半屏发白：去掉 `background-attachment: fixed`
- 冷启动不再误判在线：`_last_heartbeat` 改用 `None` 哨兵，收到第一次心跳前 `online` 恒为 False
- `TaskTimeout` 文案明确「手机端可能已经开始执行了」，避免模型自动重试写操作；迟到结果被丢弃时记日志
- `/poll` 的 `since` 语义写清：投递即出队（at-most-once），`since` 只作游标、不参与回放
- 配置里的数值项（端口/超时/质量/上限等）非法时回退默认值并 `logger.warning`，不再让一个错字搞挂插件；
  `_conf_schema.json` 给数值项补 `minimum` / `maximum`
- `phone_tap` / `phone_long_press` 用手机上报的分辨率校验坐标，越界给中文错误
- `phone_type` 用 `text.strip()` 判空（纯空格不再下发）
- `_render_ui` 表头改成「列出 N 个控件，手机共报 M 个节点」，数字与实际渲染一致
- 缩略图不再显示成「0KB」；离线排查提示里 `0.0.0.0` 换成「真实局域网 IP 或 Tailscale IP」
- 审计日志超过 5MB 自动轮转为 `audit.jsonl.1`（留 1 份），写失败至少记一条 warning；限流器清理空桶
- `phone_key` 删掉手机端并不支持的 `menu` / `power`，别再给模型假希望
- 手机端 `actWait` 回传夹取后的 `waited_ms`（以前会说「等了 60s」，实际只等 30s）

### 测试
- `smoke_test.py` 27 → 35 项：严格 bool `/result`、冷启动、超时文案、`/debug/submit` 404、healthz 鉴权差异
- `live_server.py` 20 → **48** 项：截图 TOCTOU、UI 关键词、中文名黑名单、写操作门槛、overlay 透传、
  `open_app` 诊断、坐标越界、`/debug/submit` 200、非 ASCII token、冷启动、两套白名单分离（保留/摘掉 `phone_*`）
- 新增 `tools/check_es5.py`：扫 `phone/*.js` 里禁止的 ES6 语法
- 新增 `tests/admin_console_test.py`（**41** 项）：鉴权/防爆破/白名单增删/急停清队列/自动生成 token/`off` 全 404/本机 302 补密钥

### CI
- `astrbot` 集成测试由非阻塞改为阻塞
- 新增 `ruff check plugin tests tools`（只开 E/F/I 硬错误）
- 新增 ES5 守卫 job；覆盖率只打进日志，暂不设门槛

### 计划中
- M3：相机拍照 / 录像
- M4：下载 APK 并调起安装
- M5：前台服务保活、断线重连加固
- 管理控制台 V2（安全策略开关、审计列表）

## [0.2.0] - 2026-10-31

首个可用的真机版本：**M0 + M1 完成，M2 提前完成**。

### 新增

- 插件端（`astrbot_plugin_dimensional_portal`）
  - 注册 11 个 LLM 工具：`phone_status` `phone_see` `phone_ui` `phone_tap` `phone_long_press`
    `phone_swipe` `phone_type` `phone_key` `phone_open_app` `phone_find_tap` `phone_stop`
  - aiohttp 服务：`POST /hb` 心跳、`GET /poll` 长轮询、`POST /result` 回传、`GET /healthz`
  - 可选调试口 `POST /debug/submit`（仅 `debug_token` 非空时注册）
  - 安全：QQ 白名单、20 次/分钟限流、包名黑名单、敏感界面不截图、审计日志、急停
  - `token` 为空时拒绝启动 HTTP 服务，避免裸奔
- 手机端（`phone/dimensional_portal_agent.js`，AutoX.js）
  - 心跳 + 长轮询主循环，多地址自动切换（Tailscale ↔ 局域网）
  - 截图（JPEG，可调质量）、UI 控件树（BFS 采集，弹窗/长按菜单也能读到）
  - 点击、长按、滑动、输入（`setText` → 全局 → 剪贴板三级兜底）、系统按键、打开应用、按文字查找
  - 自建 1×1 悬浮窗，取得 Android「后台启动 Activity」豁免，让微信也能从后台拉起
- 工程化
  - 三套测试：`smoke_test.py`（27 项）/ `live_server.py`（20 项）/ `import_check.py`（11 个工具）
  - GitHub Actions：冒烟测试必跑，AstrBot 集成测试非阻塞
  - 文档：README、INSTALL、PROTOCOL、PLAN、STATUS、CONSOLE_PLAN

### 修复（开发过程中定位的真机问题）

- 心跳一直失败：Rhino 里字符串没有 `getBytes()`，改用 `OutputStreamWriter` 写请求体
- UI 树取错窗口：vivo 的 `com.vivo.upslide` 系统浮层排在窗口列表首位，改为按「焦点 > 活跃 > 应用窗口」打分
- 长按弹窗读不到：UI 采集由递归 DFS 改为 BFS，避免被背景节点截断
- 键盘弹出时前台包名变成输入法：改为按窗口类型取真正的应用窗口
- `open_app` 在 AutoX 后台时静默失败：加悬浮窗豁免 + 启动后轮询确认，失败时给出可执行的提示

### 已知限制

- 锁屏无法操作；息屏截图全黑
- 需要 AutoX.js 的悬浮窗权限
- 部分 App 屏蔽无障碍服务
- 无 root 时无法注入任意按键（`enter` 靠点「发送/搜索」按钮实现）
