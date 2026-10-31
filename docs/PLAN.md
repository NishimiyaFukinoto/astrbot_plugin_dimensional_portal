# 计划与决策记录

## 里程碑

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| **M0** | 心跳 + 状态（在线/电量/分辨率/前台应用）+ 长轮询骨架 | ✅ 完成 |
| **M1** | 截图发 QQ、UI 树、点击/长按/滑动/输入/按键/开应用 | ✅ 完成 |
| **M2** | `find_text` 找文字并点、`wait` 等待 | ✅ 提前完成（M1 一起做了） |
| **M3** | 相机拍照 / 录像（`camera_photo` / `camera_video`），照片经 QQ 回传 | ⏸ 暂停（用户要求先把 M1 收尾） |
| **M4** | 下载 APK / 安装 APK（`download_apk` / `install_apk`），过包名黑名单 | ⬜ 未开始 |
| **M5** | 保活（重连、锁屏恢复）、审计、限流加固、群内二次确认 | 🟡 部分（审计/限流已有） |

### 当前状态（2026-10-31，M1 收尾）

真机（vivo V2171A / Android 15 / 1080×2400）全动作回归已通过：

| 能力 | 验证方式 | 结果 |
| --- | --- | --- |
| 心跳 / 长轮询 | `GET /healthz`、`status` | ✅ 掉线自动重连（重载插件后 4 秒内恢复） |
| 截图 → QQ | 群里收到图 | ✅ `image_b64`，q55 JPEG ≈ 117KB |
| UI 树 | `ui` / `windows` 诊断 | ✅ 弹窗/长按菜单也能读到（BFS 遍历） |
| 点击 / 长按 / 滑动 | 点「取消」、长按图标出菜单、上滑开抽屉 | ✅ |
| 输入 | 点搜索框后 `text` 输入「蓝牙」 | ✅ setText 生效 |
| 按键 | `back` / `home` | ✅（`enter`/`delete` 受无障碍限制，见 PROTOCOL） |
| 打开应用 | 微信 / QQ / 哔哩哔哩 / 设置 | ✅ 全部能从后台打开（764ms / 944ms / 2717ms） |
| 找文字并点 | `find_text {"text":"蓝牙"}` | ✅ |
| 安全 | 白名单 123456789、审计 jsonl、限流、急停 | ✅ 非白名单 987654321 被拒 |

### M3 要点
`camera_photo`：`requestScreenCapture` 不适用，改用 `images.captureScreen()` 不行（相机预览不在
UI 层），实际做法是**点相机 App 的快门按钮**，或者用 `intent` 拉起相机 App 的拍照 Intent 再
按系统 UI 操作。拍完从 `DCIM` 读最新文件，base64 回传。
`camera_video`：拉起相机 App 切录像模式 → 点开始 → sleep N 秒 → 点停止。
两个动作都要在手机端给用户**震动/日志**提示，因为录像会真的持续很久。

### M4 要点
`download_apk`：电脑端先下好（插件用 aiohttp 拉 URL 到临时目录），再把**分片**以 base64
推给手机（或让手机自己下载，二选一；手机自己下载更省流量但要处理重定向）。
`install_apk`：写文件到 `context.getExternalFilesDir` → `Intent(ACTION_VIEW, file:///… .apk)` +
`FLAG_GRANT_READ_URI_PERMISSION`，走 `FileProvider`；会弹系统安装确认框，需要用户点一下
（无 root 装不了静默）。**必须**先过 `package_blacklist` 和包名解析。

### M5 要点
- 手机端：网络断了自动重连（已做指数退避）、`events.on("exit")` 记录、AutoX.js 保活脚本
  （检测主脚本存活，掉了就拉起）。
- 插件端：群聊场景下危险动作（安装、卸载、转账类输入）二次确认（30s 超时）。
- 审计日志轮转（`audit.jsonl` 超过 5MB 就滚动）。

---

## 决策记录

| # | 决策 | 理由 |
| --- | --- | --- |
| 1 | **自研**，不用 `Tauru-t6/astrbot-phone-agent`、`acat778/astrbot_phone_control` | 那两个要么只支持截图、要么耦合了别家的执行器，改不动；自研能完全对齐「唯一大脑在 AstrBot」的约束。 |
| 2 | 手机端执行器用 **AutoX.js** | 无障碍服务级别够用（点击/滑动/输入/UI 树），不需要 root；如果换成 adb 就要开 USB 调试并常连电脑。 |
| 3 | 网络走 **Tailscale 内网**（A 方案），电脑不开公网端口 | 手机在 CGNAT 后面，公网穿透要打洞/中继，安全性差。Tailscale 免费、点对点加密。当前环境 Tailscale 还没装，先用局域网跑通（配置项已做成 `BASE` 常量，改一行就切）。 |
| 4 | **手机主动长轮询**（`GET /poll?since=&wait=12`） | 手机不能当服务端；长轮询比短轮询延迟低（有任务立刻回），比 WebSocket 在 AutoX.js 里好写（Rhino 没有成熟的 WS 客户端）。 |
| 5 | 截图走 `event.send(MessageChain([Image.fromFileSystem(path)]))` 推到 QQ | 用户明确要求「截图能直接在 QQ 里看到」，不是让模型看图。模型另有 `phone_ui` 拿文本树。 |
| 6 | **视觉模型不参与**：`phone_see` 只把图推给人看，模型拿 UI 文字 | 手机上不装第二个模型，云端视觉模型对截图做决策成本高且不稳。UI 树足够定位控件。 |
| 7 | 工具全部定义在 `main.py` | AstrBot 的 handler 绑定按 `handler_module_path == metadata.module_path` 精确匹配，子模块里的 `@filter.llm_tool` 绑不上插件实例。 |
| 8 | 不用 `@register` 装饰器 | v4.29 里已废弃（`star/register/star.py` 打 DeprecationWarning），靠 `metadata.yaml` 声明即可。 |
| 9 | 插件根目录**不放** `__init__.py` | `star_manager._get_modules` 只认 `main.py` / 同名 `.py`，命名空间包导入方式（`data.plugins.<dir>.main`）本来就不需要它。 |
| 10 | 相对导入（`from .bridge.hub import`） | 插件以 `data.plugins.astrbot_plugin_dafeiyu_phone.main` 全名导入，`__package__` 正确，相对导入可用；这样拷贝目录时不用改任何 import。 |
| 11 | 手机端用 **窗口打分** 取「当前界面」，不用 `currentPackage()` | 实体机上 `getWindows()` 里常年躺着 `com.vivo.upslide`（type=3）和输入法（type=2）；直接 `currentPackage()` 会被输入法顶掉，按顺序取第一个非 IME 窗口又会被 ups​​lide 抢走。改成 `focused(+200) > active(+60) > 应用窗口(+50)`、输入法 -1000、无 root -400、最后按窗口顺序微调。 |
| 12 | UI 采集用 **BFS** 而不是 DFS | 长按菜单/弹窗这类 overlay 节点天生「浅层、在树的后半段」，DFS 一头扎进桌面背景列表，`max_nodes` 一截断就永远照不到弹窗。换成队列遍历，并过滤掉无文字/无描述/不可点/不可输入的纯容器，`max_nodes` 才花在有用的节点上。 |
| 13 | 为「从后台打开应用」**自建一个 1×1 悬浮窗** | Android 10+ 禁止后台启动 Activity，官方豁免之一是「有 SYSTEM_ALERT_WINDOW 权限 **且当前有可见窗口**」。实测：有权限但没有窗口时，微信/QQ/哔哩哔哩全部静默失败（vivo 连跳转框都不弹）；加上窗口后三者一次成功、`bounced=False`。用 `WindowManager.addView` + `type=2038`，`alpha=0.01`（完全透明可能被判不可见）。 |
| 14 | 不用 AutoX 的 `floaty.rawWindow()` | 在 AutoX v6 上抛 `NullPointerException: ... RhinoJavaScriptEngine.getRuntime() on a null object`，子线程和顶层都抛。保留它作二级兜底，主力走原生 `WindowManager`。 |
| 15 | 加 `POST /debug/submit`（默认关闭） | 回归测试必须能确定性地下发动作，靠模型自己编参数不可复现。只有配置里 `debug_token` 非空才注册该路由，并校验 `X-Debug-Token`。 |
| 16 | `open_app` 保留「先把 AutoX 顶到前台再唤起」的兜底 | 有些应用（微信）连跳转框都不弹，直接拒绝后台唤起；兜底能救回来，代价是屏幕闪一下 AutoX。正常情况下走不到这条路径（`bounced=false`）。 |

---

## 已知风险 / 限制

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 微信、部分银行 App 屏蔽无障碍 | 点不动、读不到 UI 树 | 退到「找文字点按钮」；不行就人工。文档已写明。 |
| 无 root 不能解锁锁屏 | 锁屏后整套失效 | 屏幕常亮 / 充电；以后可接「点亮+上滑」但密码解不了。 |
| `setText` 在某些 App 失效 | 打不进字 | 三级兜底：ACTION_SET_TEXT → 全局 setText → 剪贴板 + ACTION_PASTE。 |
| 无障碍**不能注入按键事件** | 没有真正的回车/删除键 | `enter` = 找提交按钮点；`delete` = 清空输入框。已在 PROTOCOL.md 写明，不假装成功。 |
| 后台被杀 | 脚本停了，手机掉线 | 电池白名单 + 自启动；M5 做保活脚本。 |
| Tailscale 锁屏断连 | 半夜掉线 | 醒来自动重连（`reconnect()` 已有指数退避）。 |
| 手机 UI 是动态的 | 坐标点错位 | 优先 `phone_find_tap`（按文字找）而不是硬编码坐标。 |
| 分辨率/DPI 差异 | 坐标不通用 | 心跳上报真实 `w×h`，坐标按真实像素，不做 dp 换算。 |
| 误操作（乱点进设置、乱发消息） | 真实世界后果 | 白名单、限流、敏感界面不截图、急停、审计日志。 |
| 没有悬浮窗 → 后台打不开应用 | `open_app` 返回成功但屏幕不动 | 脚本启动时自建 1×1 悬浮窗（决策 13）；`phone_status` 的 `overlay` 字段可自查。 |
| vivo「应用跳转」确认框 | 第三方应用被拦，等用户点「始终打开」 | 悬浮窗就绪后该框不再出现；真出现时脚本会尝试自动点「始终打开」。 |
| 微信拒绝被后台唤起 | 连框都不弹 | `open_app` 的第二段兜底（先顶 AutoX 到前台）能救回来，屏幕会闪一下。 |

---

## 调试手册（自己踩过的坑）

1. **「手机不在线」但脚本在跑**：先用手机浏览器开 `http://电脑IP:8765/healthz`。打不开 → 网络/防火墙；
   打得开但插件说离线 → token 不一致（401，脚本日志会有）。
2. **测试时别用 `offline_after=5` 就断言离线**：心跳停了 5 秒内 `hub.online` 仍为 True，
   任务会走 `TaskTimeout` 而不是 `DeviceOffline`。要轮询等状态翻转。
3. **`policy.is_sensitive_package()` 返回 bool**，不是 `(bool, str)`；`is_sensitive_screen()` 才返回 tuple。
4. **`com.android.settings` 在内置敏感包里**，测试「普通包」要用 `com.example.notes` 之类的。
5. 冒烟测试端口用 18765，别和插件的 8765 抢。
6. **这台 vivo 上 `adb logcat` 几乎是空的**（清空后只剩 4 行），查「应用没起来」这类问题别指望 logcat，
   用 `dumpsys activity activities | grep topResumedActivity` + 截图 + 脚本返回的 `seen` 字段。
7. **`am force-stop org.autojs.autoxjs.v6` 会清掉无障碍开关**（`enabled_accessibility_services` 变 null），
   之后点「运行」会被弹到系统无障碍设置页。用 adb 补回来：
   `settings put secure enabled_accessibility_services org.autojs.autoxjs.v6/com.stardust.autojs.core.accessibility.AccessibilityService`
   + `settings put secure accessibility_enabled 1`。
8. **改完手机端脚本必须重启脚本**（force-stop → 点开脚本 → 点运行 → 过录屏授权），
   否则跑的是内存里的旧代码。Android 14/15 的录屏授权要先选「整个屏幕」再「继续」。
9. **`adb shell` 里的绝对路径会被 Git Bash 改写**（`/sdcard/x` → `C:/Program Files/Git/sdcard/x`），
   先 `export MSYS_NO_PATHCONV=1`；`adb push` 的源路径要写 `F:/...` 而不是 `/f/...`。
10. **AutoX 编辑器工具栏的坐标**（1080 宽）：`run` 在 (681,198)、`action_log` (567,198)、`edit` (57,198)；
    主页最近文件「dafeiyu_agent」在 (498,1290)。脚本路径 `/storage/emulated/0/脚本/dafeiyu_agent.js`。
