# AGENTS.md —— 项目交接说明（给下一个会话 / 给未来的自己）

> 目的：\*\*新开一个会话说「读 AGENTS.md 继续」，就能立刻接上\*\*，不用重新考古。
> 最后更新：2026-11-01　当前版本：\*\*0.3.0\*\*（M0 + M1 已完成，M2 提前完成，M3 起暂停）

\---

## 1\. 这是什么

**次元门 / Dimensional Portal** —— 让 QQ 上的 AI（AstrBot 里的模型）远程操控一台 Android 手机：
看屏幕、点击滑动、打字、打开应用、找界面上的字。

* **唯一的大脑在电脑上**（AstrBot 里的 LLM），**手机上不装第二个模型**，手机只是「手」。
* 手机端执行器是 **AutoX.js**（无障碍服务，不需要 root）。
* 手机在 CGNAT/移动网络后面，**永远是手机主动长轮询电脑**，电脑不开公网端口。
* 远程走 **Tailscale** 内网；同一 WiFi 下也支持局域网直连（双地址自动切换）。

```
QQ ←→ 腾讯 ←→ NapCat ←→ AstrBot ←→ 本插件 (aiohttp :8765) ←→ 手机 AutoX.js
                              ↑
                       LLM 调 phone\_\* 工具
```

## 2\. 目录导航

|路径|作用|
|-|-|
|`plugin/main.py`|插件入口：`class DimensionalPortalPhoneBridge(Star)`，起 HTTP 服务 + 注册 **11 个** `@filter.llm\_tool`|
|`plugin/bridge/protocol.py`|协议常量（动作名、任务 id、JSON 工具）|
|`plugin/bridge/hub.py`|任务队列 + 长轮询唤醒 + 心跳/在线状态机|
|`plugin/bridge/server.py`|aiohttp 四个端点 `/hb` `/poll` `/result` `/healthz`（+ 可选 `/debug/submit`、可选控制台 `/console` `/admin/\*`）|
|`plugin/bridge/console.html`|管理控制台单文件页面（内嵌 CSS/JS、零外部请求、移动端可用）|
|`plugin/safety/policy.py`|白名单、限流、包名黑名单、敏感界面、审计日志|
|`plugin/\_conf\_schema.json`|WebUI 里的 15 个配置项|
|`phone/dimensional\_portal\_agent.js`|**手机端 AutoX.js 脚本**（纯 ES5，Rhino 可跑；仓库里 `BASES`/`TOKEN` 是占位符）|
|`tests/smoke\_test.py`|协议层冒烟，**35 项**，不需要 AstrBot、不需要真机|
|`tests/live\_server.py`|真插件类 + 真 aiohttp + 假手机，直接调工具函数，**48 项**（必须用实例 venv python）|
|`tests/import\_check.py`|在真 AstrBot 环境验证 **11 个工具**全部注册（docstring→JSON Schema）|
|`tests/admin\_console\_test.py`|控制台端到端，**41 项**（真插件类 + 假手机 + 4 个端口，必须实例 venv）|
|`tools/make\_release\_zip.py`|打「WebUI 导入压缩包」能直接装的 zip（支持 `--help` / `--out-dir`）|
|`tools/make\_phone\_script.py`|用 `DIMENSIONAL\_PORTAL\_BASES` / `DIMENSIONAL\_PORTAL\_TOKEN` 环境变量把模板生成为带真实地址的 `phone/dimensional\_portal\_agent.local.js`（.gitignore 已忽略），推手机用这个|
|`tools/check\_es5.py`|手机端脚本 ES5 守卫：扫 `phone/\*.js` 里禁止的 ES6 语法（CI 也跑）|
|`tools/dbg.py`|走 `/debug/submit` 确定性下发动作（真机回归用）|
|`tools/reload\_plugin.py` / `dash.py`|用面板 API 重载插件 / 探测面板|
|`docs/PROTOCOL.md`|协议真相（改协议先看这个）|
|`docs/INSTALL.md`|安装、权限、排错|
|`docs/PLAN.md`|里程碑、决策记录、风险、调试手册|
|`docs/STATUS.md`|进度大纲（已完成 / 待办 P0–P6）|
|`docs/CONSOLE\_PLAN.md`|管理控制台的执行方案（V1 已实现，V2 方向仍以此为准）|

## 3\. 当前状态（2026-11-01）

* 仓库：`https://github.com/NishimiyaFukinoto/astrbot\_plugin\_dimensional\_portal`（public，main）
* CI：`.github/workflows/tests.yml` —— `smoke` 必跑，`astrbot` 集成非阻塞；最近一次 **success**
* Release：`v0.3.0`（正文 + 附件 `astrbot\_plugin\_dimensional\_portal.zip`，406 KB，含控制台背景图，可被 WebUI「导入压缩包」直接装）
* 对外呈现：logo、6 徽章、335 行 README、10 个 topics、issue/PR 模板、CHANGELOG
* 部署：**已部署**到 AstrBot 实例的 `core/data/plugins/astrbot\_plugin\_dimensional\_portal/`
* 手机：**离线**，而且手机里跑的还是改名前的旧脚本 `dafeiyu\_agent.js`（指向旧地址）；需要重新推 `dimensional\_portal\_agent.js` 并重启
* 管理控制台：**V1 已实现**（`admin\_token` 自动生成、`/console` 看状态/改白名单/急停，错 k 403、失败 10 次锁 60 秒）；
安全策略开关与审计列表留 V2，接口走 `\_ConsoleAdapter` 适配器
* 未做：M3 相机、M4 下载装 APK、M5 保活加固、控制台 V2

## 4\. 五分钟跑起来

```bash
cd dimensional-portal-phone-bridge

# 三套测试（都不需要真机）
python tests/smoke\_test.py          # 35/35
"$ASTRBOT\_INSTANCE/venv/Scripts/python.exe" tests/live\_server.py   # 48/48（必须实例 venv）
python tests/import\_check.py        # 11 个工具（需要能 import astrbot）
"$ASTRBOT\_INSTANCE/venv/Scripts/python.exe" tests/admin\_console\_test.py   # 41/41（必须实例 venv）

# 手机端脚本约束检查（ES5）
node --check phone/dimensional\_portal\_agent.js
python tools/check\_es5.py

# 打可安装的 zip → dist/astrbot\_plugin\_dimensional\_portal.zip
python tools/make\_release\_zip.py

# 部署到 AstrBot 实例（先停核心再拷，避免配置被内存覆盖）
cp -r plugin/. "<实例>/core/data/plugins/astrbot\_plugin\_dimensional\_portal/"

# 重载插件（面板端口每次由启动器随机分配，先查）
python tools/reload\_plugin.py
```

真机回归（需要手机在线且插件里 `debug\_token` 非空）：

```bash
export DIMENSIONAL\_PORTAL\_DEBUG\_TOKEN="你临时填的token"
python tools/dbg.py '{"action":"status"}'
python tools/dbg.py '{"action":"open\_app","params":{"package":"com.tencent.mm"}}'
```

## 5\. 协议要点（详见 `docs/PROTOCOL.md`）

* `POST /hb` 心跳（脚本每 10s）｜`GET /poll?since=N\&wait=12` 长轮询｜`POST /result` 回传｜`GET /healthz`
* 鉴权：`X-Token` 头或 `?token=`，`hmac.compare\_digest`；token 为空时插件**拒绝启动**（防裸奔）
* 动作（参数名**必须**一致）：
`tap{x,y}` `long\_press{x,y,duration}` `swipe{x1,y1,x2,y2,duration}` `text{text,submit}`
`key{name}`（注意是 **name**，不是 key）`open\_app{package|name}` `find\_text{text,index,exact,tap}`
`ui{max\_nodes}` `screen{quality}` `status` `wait{ms}` `windows`（诊断）
* 11 个工具：`phone\_status` `phone\_see` `phone\_ui` `phone\_tap` `phone\_long\_press` `phone\_swipe`
`phone\_type` `phone\_key` `phone\_open\_app` `phone\_find\_tap` `phone\_stop`
* 安全相关的两个开关：`allow\_screenshot\_sensitive`（默认关，敏感界面截图）、
`allow\_sensitive\_write`（默认关，敏感界面上的点击/输入/按键等写操作；关着时只能看）。
截图会按手机回报的**真实包名 + UI 文字**复查；`open\_app` 用中文名打开后也按真实包名复查黑名单。

## 6\. 手机端要点（改脚本前必看）

* 文件顶部配置区：`BASES`（Tailscale + 局域网两个地址，失败 3 次自动切换）、`TOKEN`。
* **必须给 AutoX.js 悬浮窗权限**：脚本自己挂一个 1×1、alpha=0.01 的 `TYPE\_APPLICATION\_OVERLAY` 窗口，
这是 Android 10+ 「后台启动 Activity」的**官方豁免条件**。没有它：微信/QQ 这类应用从后台打不开（系统静默丢弃）。
* 录屏授权时**要选「整个屏幕」**（默认「单个应用」会只截到 AutoX 自己）。
* UI 树是**窗口感知**的：按「焦点 > 活跃 > 应用窗口类型」给窗口打分，跳过输入法窗口；采集用 **BFS**
（DFS + `max\_nodes` 截断会永远读不到长按菜单/弹窗）。
* 无 root 时无障碍**无法注入任意按键**：`key delete` = 清空焦点输入框；`key enter` = 找并点「搜索/发送」按钮。

**改完脚本的重启流程**（AutoX 不会热加载）：

1. `adb shell am force-stop org.autojs.autoxjs.v6`
2. `adb push phone/dimensional\_portal\_agent.js /storage/emulated/0/脚本/dimensional\_portal\_agent.js`
3. 补回无障碍（force-stop 会清掉）：
`adb shell settings put secure enabled\_accessibility\_services org.autojs.autoxjs.v6/com.stardust.autojs.core.accessibility.AccessibilityService`
`adb shell settings put secure accessibility\_enabled 1`
4. `adb shell am start -n org.autojs.autoxjs.v6/org.autojs.autojs.ui.main.MainActivity`
→ 点脚本 `(498,1290)` → 点运行 `(681,198)`
5. 录屏框：`(840,2018)` 选范围 → `(696,1817)` 选「整个屏幕」→ `(540,2310)` 继续
6. `curl http://127.0.0.1:8765/healthz` 出现 `"online": true` 即成功

## 7\. 踩过的坑（都是真踩的，别重踩）

|症状|根因 / 修法|
|-|-|
|心跳一直失败、`online` 永远 false|Rhino 里 JS 字符串**没有** `getBytes()`；改用 `new java.io.OutputStreamWriter(conn.getOutputStream(), "UTF-8")`|
|`ui` 读到的是 3 个空节点|选中了 `com.vivo.upslide`（系统浮层，type=3）；改成窗口打分|
|长按菜单/弹窗读不到|DFS + `max\_nodes` 截断；改 **BFS**|
|键盘弹出后 `cur\_pkg` 变成输入法包名|`currentPackage()` 单窗口假设；改跳 IME 窗口|
|`open\_app` 返回成功但屏幕不动|后台启动限制；自建 1×1 悬浮窗拿豁免（vivo 另需 i管家「后台弹出界面」）|
|`floaty.rawWindow()` 抛 NPE|这个 AutoX 6 上不可用；改用 `WindowManager.addView`|
|插件加载失败、端口被占着不放|自建 `logging.getLogger` 不吃 AstrBot 的 `plugin\_tag` 注入；改用 `from astrbot.api import logger`|
|改了插件配置文件，重启后又被还原|AstrBot 退出时用**内存**配置回写；要么停机改，要么走 `self.config.save\_config()`|
|Git Bash 里 `adb shell ls /sdcard/` 报找不到|MSYS 路径改写；`export MSYS\_NO\_PATHCONV=1`|
|`adb push` 报 `cannot stat`|源路径写 `F:/...`，不要写 `/f/...`|
|面板端口连不上|启动器每次随机分配端口；`netstat -ano \| grep LISTENING` 查 PID 36388 的监听端口|
|`git push` 报 `unable to fork`|workspace-write 沙箱不给 fork ssh；用 `sandbox\_permissions=danger-full-access` 重试|
|ego 脚本读文件 ENOENT|ego 的 nodejs `cwd` 是 ego 安装目录；一律写绝对路径，且必须用 `import`（ESM）不能用 `require`|
|重复提交把作者身份搞错|提交前确认 `git config user.name/user.email`；`noreply` 邮箱会被 GitHub 匹配到**别人**的账号|
|NapCat 每秒重连报错、6199 没有监听、AstrBot 日志 `\[WinError 10013]`|Windows 的「动态端口保留」（winnat / Hyper-V / WSL2 / Docker）吃掉了该端口。`netsh int ipv4 show excludedportrange protocol=tcp` 看保留区间；修法：换到区间外的端口（AstrBot `platform\[0].ws\_reverse\_port` + NapCat `onebot11\_<QQ>.json` 的 `websocketClients\[0].url` **两边一起改**），或管理员 `net stop winnat` → `net start winnat`。完整复盘见 `docs/INCIDENT-2026-11-01-port-conflict.md`|
|截图全黑|手机息屏；先唤醒或开「充电时不锁屏」|
|`tests/live\_server.py` 报 `ModuleNotFoundError: No module named 'deprecated'`|用了系统 python 跑；脚本会把实例 `core/` 塞进 `sys.path` 但系统 python 没装 AstrBot 依赖。改用实例 venv：`/c/Users/YUZURU/.astrbot\_launcher/instances/9040990f-.../venv/Scripts/python.exe tests/live\_server.py`|
|控制台打不开 / 不知道 `k` 是什么|`admin\_token` 留空时首次启动自动生成并打日志「管理控制台：http://…/console?k=…」；填 `off` 是彻底关闭（四路由 404）；忘了就删掉配置里的值重启，会再生成一串|
|控制台急停测试老是不稳（`cleared` 是 0）|带假手机的实例里，长轮询会把刚排队的任务立刻取走；急停测试要在**没有假手机**的独立实例上做|

## 8\. 待办（按优先级）

* **P2**：M3 相机（`camera\_photo` / `camera\_video`）
* **P3**：M4 下载安装 APK（`download\_apk` / `install\_apk`）
* **P4**：M5 保活（前台服务、开机自启、断线告警）
* **P5**：README「界面展示」区还空着（需要一张真机截图）

## 9\. 环境事实（本机）

* 工作区：`F:\\大肥鱼\\dimensional-portal-phone-bridge`（Git Bash 里是 `/f/大肥鱼/dimensional-portal-phone-bridge`）
* AstrBot 实例：`C:\\Users\\YUZURU\\.astrbot\_launcher\\instances\\9040990f-1e86-46fc-85a6-aee6cd7aa47c`

  * 核心：`core/`（v4.29.4）；Python：`venv/Scripts/python.exe`（3.12）
  * 插件部署：`core/data/plugins/astrbot\_plugin\_dimensional\_portal/`
  * 插件配置：`core/data/config/astrbot\_plugin\_dimensional\_portal\_config.json`（**utf-8-sig，带 BOM**）
  * 审计日志：`core/data/plugin\_data/astrbot\_plugin\_dimensional\_portal/audit.jsonl`
* 端口：插件 `8765`；NapCat 反向 WS `6199`（曾因 Windows 动态端口保留被吃掉、临时改过 16100，后已抢回 6199，见第 7 节）；NapCat WebUI `6099`；面板端口随机
* adb：`/d/MAA/adb/platform-tools/adb.exe`（不在 PATH）
* Tailscale：`/c/Program Files/Tailscale/tailscale.exe`（不在 PATH）
* 常用环境变量：`ASTRBOT\_INSTANCE`、`ASTRBOT\_DASH\_PORT`、`DIMENSIONAL\_PORTAL\_BRIDGE`、`DIMENSIONAL\_PORTAL\_DEBUG\_TOKEN`

> 约定：\*\*没有得到用户明确许可，不要改文件\*\*（这条是用户立的规矩）；改完要跑测试、提交时写清楚改了什么。

