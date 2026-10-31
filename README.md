# 我的圣剑 · Excalibur（excalibur-phone-bridge）

让 QQ 上的「大肥鱼」远程操控你的 Android 手机。**唯一的大脑是 AstrBot 里的模型，手机上不装第二个模型。**

```
QQ  ←→  腾讯  ←→  NapCat  ←→  AstrBot  ←→  本插件 (aiohttp :8765)  ←→  手机 AutoX.js
                                          ↑
                                   LLM 调 phone_* 工具
```

手机在 CGNAT 后面不能当服务端，所以永远是**手机主动长轮询**电脑；电脑不开公网端口。

> 当前状态：**M0 + M1 已完成并在真机验证**；M2（找文字）已提前完成；M3 相机 / M4 装 APK / M5 保活 暂停。
> 详细进度见 [docs/STATUS.md](docs/STATUS.md)；下一步的「管理控制台」方案见 [docs/CONSOLE_PLAN.md](docs/CONSOLE_PLAN.md)（讨论稿，未实现）。

## 依赖与兼容性

| 组件 | 要求 | 说明 |
| --- | --- | --- |
| AstrBot | ≥ 4.29.0（实测 4.29.3） | 插件走 `metadata.yaml` + `_conf_schema.json`，`< 4.10` 不再支持 |
| Python | 3.10+（实测 3.12.16） | 只用标准库 + aiohttp（AstrBot 自带） |
| 手机端 | Android 8+（实测 Android 15） | 需要 AutoX.js（无障碍，不需要 root） |
| 网络 | 局域网或 Tailscale 等内网 | 不需要公网 IP、不需要端口映射 |
| 即时通讯 | NapCat（aiocqhttp 反向 WS） | 其他 AstrBot 适配器理论上也行，未测 |

## 现在能做什么（M0 + M1）

| 能力 | 工具 |
| --- | --- |
| 看状态（在线/电量/分辨率/前台应用/权限） | `phone_status` |
| 截图发到 QQ + 界面文字摘要 | `phone_see` |
| 读界面控件树（文字、坐标、可点/输入） | `phone_ui` |
| 点击 / 长按 / 滑动 | `phone_tap` `phone_long_press` `phone_swipe` |
| 输入文字（含提交） / 按系统键 | `phone_type` `phone_key` |
| 打开应用 | `phone_open_app` |
| 找文字并点（M2 提前做了） | `phone_find_tap` |
| 急停 | `phone_stop` |

M3（相机拍照录像）、M4（下载安装 APK）、M5（保活/审计/限流加固）见 [docs/PLAN.md](docs/PLAN.md)。
M3 目前暂停。

**进度大纲（什么做完了、什么没做、下一步）：[docs/STATUS.md](docs/STATUS.md)**

### 真机验证状态（2026-10-31，vivo V2171A / Android 15 / 1080×2400）

- 心跳/长轮询 ✅（掉线自动重连）
- 截图 → QQ ✅（q55 JPEG ≈ 117KB）
- UI 树 ✅ 长按菜单/弹窗也能读到
- tap / long_press / swipe / text / key ✅
- `open_app` ✅ 微信 944ms、QQ 764ms、哔哩哔哩 2717ms，都能从后台打开
  （靠脚本自建的 1×1 悬浮窗拿到 Android 的后台启动豁免；必须给 AutoX.js 悬浮窗权限）
- `find_text` / `wait` ✅
- 白名单 / 限流 / 敏感界面 / 审计 / 急停 ✅
- 远程可用 ✅ 走 Tailscale 内网（电脑 `100.x.x.1` ↔ 手机 `100.x.x.2`），
  手机用流量也能连；脚本内置「连不上就切局域网地址」的兜底

## 目录

```
excalibur-phone-bridge/
├── plugin/                  → 拷到 AstrBot 的 core/data/plugins/astrbot_plugin_excalibur
│   ├── main.py              插件入口：起 HTTP 服务 + 注册 11 个 LLM 工具
│   ├── metadata.yaml        插件元数据
│   ├── _conf_schema.json    WebUI 里的配置项
│   ├── bridge/
│   │   ├── protocol.py      协议常量（动作名、任务 id、JSON 工具）
│   │   ├── hub.py           任务队列 + 长轮询唤醒 + 心跳状态机
│   │   └── server.py        aiohttp：/hb /poll /result /healthz（+ 可选 /debug/submit）
│   └── safety/
│       └── policy.py        白名单、限流、包名黑名单、敏感界面、审计日志
├── phone/
│   └── excalibur_agent.js     手机端 AutoX.js 脚本
├── tests/
│   ├── smoke_test.py        假手机客户端跑通整条链路（不需要真机）
│   ├── live_server.py       真插件类 + 真 aiohttp + 假手机，直接调工具函数
│   └── import_check.py      在真实 AstrBot 环境里验证工具注册
├── tools/
│   ├── dbg.py               通过 /debug/submit 确定性下发动作（回归用）
│   ├── uiview.py            把 UI 树结果整理成可读文本
│   └── dash.py / reload_plugin.py   面板 API 探测与插件重载
│        环境变量：ASTRBOT_INSTANCE（实例目录）、ASTRBOT_DASH_PORT（面板端口）、
│                  EXCALIBUR_BRIDGE / EXCALIBUR_DEBUG_TOKEN（回归用）
└── docs/
    ├── STATUS.md            进度大纲（已完成 / 待办 P0–P5 / 平台边界）
    ├── CONSOLE_PLAN.md      管理控制台执行方案（讨论稿）
    ├── PROTOCOL.md          通信协议（改协议先看这个）
    ├── INSTALL.md           安装与排错
    └── PLAN.md              里程碑、决策记录、已知风险、调试手册
```

## 快速开始

1. 手机和电脑都装 Tailscale 并登录同一账号，记下电脑的 `100.x` 地址
   （`"C:\Program Files\Tailscale\tailscale.exe" ip -4`）。
2. 手机上装 AutoX.js，把脚本顶部的 `BASES` 第一个地址改成电脑的 `100.x`、`TOKEN` 对上插件配置，
   运行并允许「屏幕录制」+ 开无障碍 + 开悬浮窗权限。
3. 把 `plugin/` 拷到 AstrBot 的插件目录，重载插件（见 [docs/INSTALL.md](docs/INSTALL.md)）。
4. AstrBot WebUI → 插件配置里填好自己的 QQ 号（白名单）。
5. 在 QQ 里对「大肥鱼」说：**看看手机现在什么界面**。

## 自测

```bash
# 不需要真机：假手机客户端跑通 心跳 → 长轮询 → 执行 → 回传（27 项）
python tests/smoke_test.py

# 真插件类 + 真 aiohttp + 假手机，直接调工具函数（20 项）
python tests/live_server.py

# 用 AstrBot 自己的 venv 验证 11 个工具都能注册（docstring → JSON Schema）
python tests/import_check.py

# 真机回归（需要插件里临时配了 debug_token）
EXCALIBUR_DEBUG_TOKEN=xxx python tools/dbg.py '{"action":"status"}'
```

Windows 控制台是 GBK，脚本输出里的 emoji 可能触发 `UnicodeEncodeError`；
三套测试都自带了 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，重定向到文件再读即可。

## 安全须知

- **只在可信网络里跑**。插件默认监听 `0.0.0.0:8765`，没有内置 TLS；对外暴露请套反向代理或只绑 Tailscale 地址。
- **`token` 必须改**。插件配置里的手机端 `token` 留空时，插件会拒绝启动 HTTP 服务（防止裸奔）。
- **白名单必须非空**。`allowed_user_ids` 为空等于不限制，任何能给机器人发消息的人都能驱动你的手机。
- **敏感界面默认不截图**（支付、密码、验证码、银行类包名），需要时再单独打开。
- **调试口默认关闭**。`debug_token` 非空才会注册 `/debug/submit`，它能绕过白名单与限流，用完请清空。

## 免责声明

本项目仅供个人在自己的设备上做自动化实验使用。使用者需自行确保：

- 手机是**你自己的**，或你已获得机主的明确授权；
- 使用方式符合 QQ / 腾讯、AstrBot、AutoX.js 及所在地法律法规的要求；
- 不要用它做刷量、群控、监控他人等违规用途。

由此产生的一切后果由使用者承担，作者不承担任何责任。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 插件宿主与 LLM 工具注册
- [NapCat](https://github.com/NapNeko/NapCatQQ) —— QQ 协议端
- [AutoX.js](https://github.com/autox-community/AutoX) —— 手机端无障碍自动化

## License

[MIT](LICENSE)
