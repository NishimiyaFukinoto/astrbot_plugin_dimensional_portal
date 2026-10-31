# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划中
- M3：相机拍照 / 录像
- M4：下载 APK 并调起安装
- M5：前台服务保活、断线重连加固
- 管理控制台（网页里改白名单与安全开关，见 `docs/CONSOLE_PLAN.md`）

## [0.2.0] - 2026-10-31

首个可用的真机版本：**M0 + M1 完成，M2 提前完成**。

### 新增

- 插件端（`astrbot_plugin_excalibur`）
  - 注册 11 个 LLM 工具：`phone_status` `phone_see` `phone_ui` `phone_tap` `phone_long_press`
    `phone_swipe` `phone_type` `phone_key` `phone_open_app` `phone_find_tap` `phone_stop`
  - aiohttp 服务：`POST /hb` 心跳、`GET /poll` 长轮询、`POST /result` 回传、`GET /healthz`
  - 可选调试口 `POST /debug/submit`（仅 `debug_token` 非空时注册）
  - 安全：QQ 白名单、20 次/分钟限流、包名黑名单、敏感界面不截图、审计日志、急停
  - `token` 为空时拒绝启动 HTTP 服务，避免裸奔
- 手机端（`phone/excalibur_agent.js`，AutoX.js）
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
