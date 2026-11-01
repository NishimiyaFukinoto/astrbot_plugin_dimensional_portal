# 我的圣剑 · 进度大纲

> 快照时间：2026-10-31 22:40（Asia/Shanghai）
> 本文是**进度大纲**（现在什么能用、什么没做）。协议细节见 [PROTOCOL.md](PROTOCOL.md)，安装步骤见 [INSTALL.md](INSTALL.md)，决策记录与踩坑全集见 [PLAN.md](PLAN.md)。

## 0. 一句话

QQ 上的「大肥鱼」通过 AstrBot 里的大模型，远程操控一台 Android 15 手机：看界面、点按滑动、输入、开应用、拍照录像、装应用。**唯一的大脑在电脑上，手机上只有一双「手」（AutoX.js 无障碍脚本），不装第二个模型。**

## 1. 架构

```
   你（QQ）
     │  私聊 / 群聊
     ▼
   腾讯 → NapCat（反向 WS，端口 6199）
     │
     ▼
   AstrBot v4.29.4 ── 插件 astrbot_plugin_excalibur
     │                     ├─ 注册 11 个 LLM 工具 phone_*
     │                     └─ aiohttp 服务 :8765  ◄──── 长轮询（手机主动来要任务）
     │                                  │
     ▼                                  ▼
   大模型决定调哪个工具          手机 AutoX.js 脚本
   （手机上不跑模型）             ├─ 无障碍：读界面树 / 点按滑动输入
                                  ├─ MediaProjection：截图
                                  └─ 1×1 悬浮窗：拿「后台启动应用」豁免
```

**为什么手机主动轮询**：手机在运营商 CGNAT 后面，没有公网地址、不能当服务端；电脑也不开公网端口。所以手机每 10 秒发一次心跳，并挂一个 12 秒左右的长轮询取任务。截图由插件 `event.send(Image.fromFileSystem(...))` 直接推到 QQ。

**网络**：走 Tailscale 内网（电脑 `100.x.x.1` ↔ 手机 `100.x.x.2`），手机在外用流量也能连回家里的电脑。脚本内置双地址（Tailscale / 局域网）自动切换。

## 2. 里程碑进度

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 心跳 + 状态 | ✅ 完成 |
| M1 | 截图 / UI 树 / 点按滑 / 输入 / 按键 / 开应用 | ✅ 完成（真机验证） |
| M2 | 找文字点击 / 等待 | ✅ 完成（提前做了） |
| M3 | 相机拍照、录像 | ⏸ 暂停（用户要求先收尾 M1） |
| M4 | 下载并安装 APK | ⬜ 未开始 |
| M5 | 保活 / 审计 / 限流 | ⚠️ 部分（审计+限流+急停已完成，保活未做） |
| 网络 | Tailscale 内网 | ✅ 完成 |
| 安全 | 白名单 / 限流 / 敏感界面 / 审计 / 急停 | ✅ 完成（群内二次确认未做） |
| 控制台 V1 | `/console` 看状态、改白名单、急停 | ✅ 完成（2026-11-01，`ec175f4`） |
| 控制台 V2 | 安全策略开关、审计列表 | ⬜ 未开始 |

## 3. ✅ 已完成

### 3.1 插件端（Python，约 1450 行）

- **11 个 LLM 工具**：`phone_status` `phone_see` `phone_ui` `phone_tap` `phone_long_press` `phone_swipe` `phone_type` `phone_key` `phone_open_app` `phone_find_tap` `phone_stop`
- 任务中枢（`bridge/hub.py`）：任务队列、长轮询唤醒、心跳状态机、超时/离线判定、统计
- HTTP 服务（`bridge/server.py`）：`/hb` `/poll` `/result` `/healthz`，可选 `/debug/submit`，Token 鉴权
- 安全策略（`safety/policy.py`）：白名单、20 次/分钟限流、包名黑白名单、敏感界面拦截、JSONL 审计、急停
- 配置项 13 个（`_conf_schema.json`），WebUI 可改

### 3.2 M0 心跳与状态

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| 手机每 10s 心跳（电量/分辨率/前台包名/权限状态） | ✅ | `/hb` 返回 `{"protocol":1,...}` |
| 电脑判断在线/离线（40s 无心跳判离线） | ✅ | 停心跳后 `/healthz` → `online:false` |
| 掉线自动重连 | ✅ | 插件重载后 4 秒内 `online` 恢复 `true`；当前已连续在线 1.5 小时 |
| `phone_status` 工具 | ✅ | 返回 `vivo V2171A / Android 15 / 1080×2400 / 电量 / 无障碍 / 截屏 / 悬浮窗` |

### 3.3 M1 操作能力（全部真机验证）

| 能力 | 工具 | 实测 |
| --- | --- | --- |
| 截图并发到 QQ | `phone_see` | q55 JPEG ≈ 117KB，QQ 里能看到 |
| 读界面控件树 | `phone_ui` | 40 个带文字节点、带坐标/可点/可输入；长按弹窗也能读到 |
| 点击 | `phone_tap` | 点「取消」「蓝牙」均生效 |
| 长按 | `phone_long_press` | 长按桌面微信弹出菜单 |
| 滑动 | `phone_swipe` | 上滑打开应用抽屉 |
| 输入文字 | `phone_type` | 设置里输入「蓝牙」，EditText 内容变化 |
| 系统按键 | `phone_key` | home / back / recents / volume；`enter`/`delete` 有限制（见 §5） |
| 打开应用 | `phone_open_app` | **QQ 764ms、微信 944ms、哔哩哔哩 2717ms**，均从后台成功、无兜底 |
| 急停 | `phone_stop` | 清空任务队列 |

### 3.4 M2 找文字 / 等待（提前完成）

| 能力 | 工具 | 实测 |
| --- | --- | --- |
| 找文字并点 | `phone_find_tap` | 找到「蓝牙, 已关闭」并点开蓝牙页；找不到时返回候选文字列表 |
| 等待 | 内部 `wait` 动作 | 最大 30s |

### 3.5 手机端执行器（AutoX.js，1116 行）

- 截图 → base64 回传 → 插件落盘 → 推进 QQ
- UI 树：**BFS 采集** + 只输出有信息量节点 + **窗口打分**（避开 vivo `com.vivo.upslide` 浮层与输入法）
- 输入三级兜底：`ACTION_SET_TEXT` → 全局 `setText` → 剪贴板粘贴
- **1×1 悬浮窗守卫**：拿 Android「后台启动 Activity」豁免，微信/QQ/B站都能从后台拉起
- 双地址自动切换（Tailscale ↔ 局域网，连续 3 次失败换一个）+ 心跳失败指数退避（2s→8s）
- 心跳自报 `overlay` / `overlay_note`，一眼看出悬浮窗守卫是否在位

### 3.6 三个真机 bug 与最终修法（都值得记住）

| 现象 | 根因 | 修法 |
| --- | --- | --- |
| 后台调 `open_app` 静默失败，屏幕不动 | Android 10+ 禁止后台应用启动 Activity；豁免条件之一是「有权限**且当前有可见悬浮窗**」 | 脚本启动时自建 1×1、alpha 0.01 的悬浮窗（`WindowManager` + `type=2038`）。`floaty.rawWindow()` 在 AutoX 6 上必抛 NPE，不能用 |
| 键盘弹出时前台包名变成输入法 | 旧实现用 `currentPackage()`，只看单一窗口 | 遍历 `getWindows()` 并按「焦点 +200 / 活跃 +60 / 应用窗口 +50 / 系统窗口 −50 / 输入法 −1000 / 无 root −400」打分 |
| 长按菜单、弹窗读不到 | UI 采集用递归 DFS，被 `max_nodes` 截断在深层容器里，永远走不到浅层弹窗 | 改 BFS 队列遍历；并过滤掉无文字/不可点的纯容器节点 |

### 3.7 安全

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| QQ 号白名单 | ✅ | 当前 `123456789,987654321`；名单外的号会被 `_gate` 拒绝并写 `deny_user` 审计 |
| 限流 | ✅ | 默认 20 次 / 60 秒（滑动窗口） |
| 包名黑名单 / 敏感界面 | ✅ | 微信/支付宝/银行/设置等界面拒截图（可配置放行）；敏感界面写操作默认拒绝，按真实包名复查黑名单 |
| 审计日志 | ✅ | `data/plugin_data/astrbot_plugin_excalibur/audit.jsonl`，每条任务一行（已累积 117 条） |
| 急停 | ✅ | `phone_stop` 清队列 |
| X-Token 鉴权 | ✅ | 头或 query，`hmac.compare_digest` 比较 |

### 3.8 工程化

| 项目 | 状态 |
| --- | --- |
| 假手机端到端测试 `tests/smoke_test.py` | ✅ 35/35 通过 |
| 真插件类 + 真 aiohttp 测试 `tests/live_server.py` | ✅ 46/46 通过（需实例 venv python） |
| 真实 AstrBot 环境里 11 个工具注册 `tests/import_check.py` | ✅ 全部通过 |
| 调试下发端点 `/debug/submit` | ✅ 回归时用过；现已清空 `debug_token` 关闭 |
| 文档（README / INSTALL / PROTOCOL / PLAN / STATUS） | ✅ 已写 |
| 插件已部署并重载生效 | ✅ 与工作区源码 `diff` 一致，日志有 11 条 `Added llm tool: phone_*` |

### 3.9 网络（Tailscale，已完成）

- 电脑 `100.x.x.1`，手机 `100.x.x.2`（`tailscale status` 里 `v2171a android`），同一账号 `your-account@`。
- 脚本双地址切换：`BASES = ["http://100.x.x.1:8765", "http://192.168.1.10:8765"]`。
- 连通实测：`netstat` 里 `100.x.x.1:8765 ← 100.x.x.2:40298 ESTABLISHED`，即走 Tailscale 而非局域网。
- 延迟（DERP 中继下）：`status` 461ms、`ui`(8 节点) 1327ms、`screen` 4278ms；打洞成直连后会更快。
- Windows 防火墙三个配置文件当前全是关闭状态，所以没加放行规则也能连（开了防火墙就按 INSTALL 里的 `netsh` 命令放行）。
- 遗留风险：手机休眠/锁屏时 Tailscale 可能掉线，脚本会退避重连。

## 4. ⬜ 待办（按优先级）

### P0 安全收尾 ✅ 已完成
- `debug_token` 已清空（2026-10-31 22:56），`/debug/submit` 现在返回 404；需要真机回归时再临时填一个值并重载插件。

### P1 体验补强
- **管理控制台 V1 已完成**（`plugin/bridge/console.html` + `/console` `/admin/state` `/admin/config` `/admin/stop`）：
  浏览器里看手机状态、增删 QQ 白名单（改完即时生效，无需重载）、急停；`admin_token` 留空时首次启动自动生成并写盘，填 `off` 整块关闭。
  V2 待做：安全策略开关（限流 / 敏感截图 / 写操作 / 超时）与审计列表。
- **锁屏唤醒**：息屏时截图全黑、点击落锁屏（见 §5）。
- **危险动作群内二次确认（30s）**：代码里还没有，目前点按直接执行。
- 输入法兜底实测：少数自绘输入框 `setText` 会失效。

### P2 M3 相机（暂停中）
- `camera_photo` / `camera_video`：目前收到动作直接报「还没实现（M3/M4 里程碑）」。
- 要做：系统相机 `Intent(ACTION_IMAGE_CAPTURE / ACTION_VIDEO_CAPTURE)` 或 MediaProjection 录制 → 拿文件 → 走 `/result` 回传 → 插件侧 `phone_camera` 发 QQ。

### P3 M4 下载并安装 APK
- 协议占位 `download_apk` / `install_apk`。
- 要做：手机端下载 → 校验 → `ACTION_VIEW` + `application/vnd.android.package-archive` 触发安装（无 root 时依赖「安装未知应用」授权 + 用户点确认）→ 进度回传。

### P4 M5 保活与加固

| 子项 | 状态 |
| --- | --- |
| 断线重连（心跳失败指数退避） | ✅ 已完成 |
| 审计日志 / 限流 | ✅ 已完成 |
| 危险动作群内二次确认 | ⬜ 未做 |
| 脚本开机自启 / 崩溃自动重启（前台服务保活） | ⬜ 未做，目前靠手动点「运行」 |
| 手机锁屏时的处理（唤醒屏幕） | ⬜ 未做 |
| 长连接稳定性压测（跨夜） | ⬜ 未做 |

### P5 收尾杂项

| 项目 | 状态 | 建议 |
| --- | --- | --- |
| `debug_token` | ✅ 已清空 | `/debug/submit` 已关闭（POST 返回 404） |
| `AGENT_VERSION` | ✅ 0.2.0 | 与 `plugin/metadata.yaml` 的版本号保持一致 |
| 工作区残留 `data/cmd_config.json`、`data/t2i_templates/` | ✅ 已忽略 | `.gitignore` 排除了 `data/`，不影响提交 |
| `tools/dash.py` 面板端口写死 `6185` | ✅ 已修 | 改成 `ASTRBOT_DASH_PORT`，实例目录走 `ASTRBOT_INSTANCE` 或自动发现 |

### P6 开源就绪

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 个人数据脱敏 | ✅ | 文档/脚本里的 IP、QQ 号、Tailscale 账号、本机路径全部换成占位符 |
| 硬编码 token | ✅ | 插件 `token` 默认空；手机脚本 `TOKEN` 为占位符；`tools/dbg.py` 改为环境变量 |
| 防裸奔 | ✅ | `token` 为空时插件拒绝启动 HTTP 服务并打 error 日志 |
| 许可证 | ✅ | MIT（`LICENSE`，署名 `我的圣剑`，可自行改） |
| 忽略规则 | ✅ | `.gitignore`（`tmp/`、`data/`、`__pycache__`、截图）+ `.gitattributes`（统一 LF） |
| README 开源化 | ✅ | 依赖与兼容性、安全须知、免责声明、致谢、License |
| 版本号 | ✅ | 插件与手机脚本统一 `0.2.0` |
| 仓库地址 | ✅ 已填 | `https://github.com/NishimiyaFukinoto/astrbot_plugin_excalibur` |

## 5. ⚠️ 平台边界（不是 bug，改不了）

1. **锁屏**：没有 root 时无障碍无法解锁；锁屏期间截图全黑、点击无效。建议把自动锁屏调长，或在开发者选项里开「充电时不锁屏」。
2. **按键注入**：无障碍拿不到「按回车」这种纯按键事件。所以 `key enter` 是「找搜索/发送/确定按钮去点」，找不到会明确报错；`key delete` 是「清空当前焦点输入框」而不是退格。
3. **微信/银行类应用**：微信会拒绝后台唤起（靠悬浮窗豁免绕过）；部分金融类应用屏蔽无障碍读屏，只能靠截图 + 坐标点。
4. **输入法**：`setText` 对少数输入框无效，已做三级兜底。
5. **vivo 特有**：后台启动第三方应用可能弹「应用跳转」确认框（选过「始终打开」后不再弹）；`adb logcat` 几乎是空的，排错别指望它。

## 6. 当前运行状态快照（写这份文档时）

- 插件服务：`GET http://127.0.0.1:8765/healthz` → `{"ok":true,"online":true,"queue":0,"uptime_s":5665}`
- 手机在线已 1.5 小时：`vivo V2171A / Android 15 / 1080×2400 / 电量 73 / 无障碍 ✓ 截屏 ✓ 悬浮窗 ✓`
- 手机此刻**息屏**（`screen_on:false`，前台 `com.android.systemui`）——息屏时截图会全黑，需要先亮屏解锁。
- AstrBot 进程同时监听 `8765`（插件）、`127.0.0.1:16100`（NapCat 反向 WS）、以及启动器随机分配的面板端口。
- 插件配置：`allowed_user_ids = "123456789,987654321"`、`debug_token = ""`（`/debug/submit` 已关闭）。
- 审计日志已 117 行。

## 7. 环境依赖

### 电脑端（已完成）
- AstrBot v4.29.4 + NapCat（启动器管理：面板端口随机、NapCat 反向 WS 用 16100）
- 插件已部署：`<实例>/core/data/plugins/astrbot_plugin_excalibur`
- Tailscale 已登录（电脑 `100.x.x.1`）；防火墙当前全关，无需放行

### 手机端（重启手机后要重做第 2–5 步）
1. 装 AutoX.js（包名 `org.autojs.autoxjs.v6`）
2. 开无障碍服务（`am force-stop` 或重启后会被关掉）
3. 把 `phone/excalibur_agent.js` 放到 `/storage/emulated/0/脚本/`
4. 点「运行」，系统录屏弹窗里选 **整个屏幕** → 「继续」
5. 给 AutoX.js 悬浮窗权限（后台启动应用的豁免条件）
6. 电池优化白名单（防后台被杀）

## 8. 代码规模

| 部分 | 行数 |
| --- | --- |
| 插件 Python（`plugin/`） | 1450 |
| 手机脚本（`phone/excalibur_agent.js`） | 1116 |
| 测试（`tests/`，3 个脚本） | 664 |
| 调试工具（`tools/`，5 个脚本） | 386 |
| **合计（不含文档）** | **3616** |

## 9. 下一步建议

按「投入产出比」排序，等你挑：

1. **先用起来**：`debug_token` 已清掉，直接在 QQ 里让大肥鱼看界面 / 点按 / 开应用，跑几天看稳定性——这是唯一能暴露真实问题的办法。
2. **M5 保活**：手机重启后脚本要能自己起来，否则每次都得手动点「运行」，实际使用最痛。
3. **锁屏唤醒 + 群内二次确认**：两个可用性缺口，实现都不大。
4. **M3 相机**：QQ 里说「拍张照给我」，演示效果好，实现不难。
5. **M4 装 APK**：要处理「安装未知应用」授权和用户确认，麻烦但有用。
6. 注意：Tailscale 相当于把手机操控入口挂在你自己的内网里，谁能登你的 Tailscale 账号谁就能操控手机——账号安全（两步验证）要自己管好。
