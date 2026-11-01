# 安装与排错

一共四步：手机装 AutoX.js → 改脚本 → 拷插件 → 配置。

---

## 1. 手机端：装 AutoX.js

1. 装 AutoX.js（Auto.js 的开源续作，离线版或最新版都行）。
2. 打开 App，**允许**：无障碍服务（设置 → 无障碍 → AutoX.js → 开）、悬浮窗（**必开**，见下面的说明）、
   电池优化白名单（设置 → 电池 → 无限制，否则后台会被杀）。
   > **悬浮窗必须给，而且脚本会自己挂一个看不见的悬浮窗。**
   > Android 10 以后禁止后台应用启动别的应用，官方只留了几个豁免，其中一个就是
   > 「应用有悬浮窗权限 **并且当前确实有窗口在显示**」。所以脚本一启动就会用
   > `WindowManager` 加一个 1×1 像素、几乎全透明的悬浮窗（这是合规的豁免条件，
   > 不是耍赖），之后从后台打开微信/QQ/哔哩哔哩才能成功。
   > 少了这个权限，`open_app` 会「返回成功但屏幕不动」——vivo 上连「是否允许跳转」的框都不弹。
   > 用 `phone_status` 能看到 `overlay: true`（`overlay_note` 会写失败原因）。
   > vivo/iQOO 如果仍然打不开，再去 **i管家 → 应用管理 → 权限管理 → 权限 → 后台弹出界面**
   > 里允许 AutoX.js。
3. 把 `phone/excalibur_agent.js` 传到手机（QQ 发给自己 → 用 AutoX.js 打开，或放到
   `/sdcard/脚本/` 后在 AutoX.js 里导入）。**改完脚本一定要在 AutoX.js 里重新点「运行」**，
   否则手机上跑的还是旧代码。
4. 改文件顶部的地址和 token（`BASES` 是候选服务器列表，第一个 Tailscale、第二个局域网，
   **连不上会自动切换**，两个都连不上就来回重试）：

```js
var BASES = [
    "http://100.x.x.1:8765",   // Tailscale（电脑的 100.x 地址，手机用流量也能连）
    "http://192.168.1.10:8765"   // 局域网（同一个 WiFi 时延迟更低）
];
var TOKEN = "<你的手机端 token>";         // 必须和插件配置里的 token 一致
```

5. 点运行。第一次会弹「录制或共享屏幕」授权，**允许**（截图要用）。
   Android 14/15 的这个框多了一步：先在「共享或录制的范围」里选 **整个屏幕**（默认是「单个应用」，
   选了单个应用的话截出来的图只有 AutoX 自己的界面），再点 **继续**。选完会记住，之后一般不再弹。
6. 日志出现 `✅ 心跳成功` 就通了。想先验证网络，用手机浏览器打开
   `http://100.x.x.1:8765/healthz`，能看到 JSON 就说明网络没问题。

> **为什么用 Tailscale**：手机在运营商 CGNAT 后面当不了服务端，电脑也不该开公网端口。
> 两端登录同一个账号后各有一个固定的 `100.x` 地址，手机用流量也能连回家里的电脑。
> 查地址：电脑 `"C:\Program Files\Tailscale\tailscale.exe" ip -4`，
> 手机 `tailscale status` 里 `android` 那一行（本项目实测：电脑 `100.x.x.1`、手机 `100.x.x.2`）。
> 两端要能互相 ping 通：`tailscale ping 100.x.x.2`（显示 `via DERP` 也算通，只是走中继）。
> 换电脑/换网络只改 `BASES` 数组；Windows 防火墙如果开着，记得放行 8765（见第 2 节末尾）。

---

## 2. 电脑端：装插件

把本仓库的 `plugin/` 整个目录拷到 AstrBot 的插件目录，并**改名为插件名**：

```
<AstrBot 实例目录>\core\data\plugins\astrbot_plugin_excalibur\
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── bridge\
└── safety\
```

即 `plugin\*` → `...\core\data\plugins\astrbot_plugin_excalibur\*`。

拷完在 WebUI → 插件管理 → 重载插件，或者重启 AstrBot。
（WebUI 端口由启动器分配，每次启动可能不一样，以启动器窗口里显示的地址为准；
找现在监听哪个端口：`netstat -ano | findstr LISTENING`，同进程还会占 8765 和 NapCat 反向 WS 端口（本机是 16100，默认 6199）。）

日志里应该看到：

```
[excalibur] 就绪：请让手机上的 AutoX.js 脚本连 http://<电脑IP>:8765
```

如果没有，看是不是端口被占用（`netstat -ano | findstr 8765`），改配置里的 `listen_port` 再重载。

### Windows 防火墙

第一次启动 aiohttp 监听时 Windows 会弹「是否允许访问」，**勾选专用网络并允许**。
没弹或点了取消，就手动加一条入站规则：

```
netsh advfirewall firewall add rule name="excalibur 8765" dir=in action=allow protocol=TCP localport=8765
```

（这条需要在管理员 PowerShell 里跑。）

---

## 3. 配置

WebUI → 插件管理 → 我的圣剑 → 配置：

| 配置项 | 建议值 | 说明 |
| --- | --- | --- |
| `token` | 自己改一个长随机串（**留空插件不会启动 HTTP 服务**） | 和手机脚本 `TOKEN` 一致 |
| `allowed_user_ids` | **你自己的 QQ 号** | 留空 = 谁都能操控你的手机，务必填 |
| `listen_host` | `0.0.0.0` | 局域网/Tailscale 都能连 |
| `listen_port` | `8765` | 被占用就换 |
| `screenshot_quality` | `55` | 图糊就调高，太大就调低 |
| `allow_screenshot_sensitive` | `false` | 微信/支付宝/银行界面默认不截图 |

---

## 4. 验证

在 QQ 里对「大肥鱼」说：

- 「看看手机现在什么界面」→ 应收到一张截图 + 界面文字摘要
- 「手机连上了吗」→ 应收到电量/分辨率/前台应用

### 不用 QQ 也能自检（开发用）

插件配置里 `debug_token` 非空时，会额外开一个 `POST /debug/submit`（要带 `X-Debug-Token` 头），
可以直接下发动作给手机、拿回结果，不经过模型。**当前配置里是空值，这个路由没注册（`POST` 会返回 404）**，
要做真机回归时临时填一个随机串再重载插件即可，用完后记得清空。

```bash
# 看设备状态（含 overlay 是否就绪）
curl -s -X POST http://127.0.0.1:8765/debug/submit \
  -H "X-Debug-Token: <配置里的 debug_token>" \
  -H "Content-Type: application/json" \
  -d '{"action":"status"}'

# 打开微信并确认前台真的变了
curl -s -X POST http://127.0.0.1:8765/debug/submit \
  -H "X-Debug-Token: <debug_token>" -H "Content-Type: application/json" \
  -d '{"action":"open_app","params":{"package":"com.tencent.mm"}}'

# 列出所有窗口和打分（排查 UI 树取错窗口）
curl -s -X POST http://127.0.0.1:8765/debug/submit \
  -H "X-Debug-Token: <debug_token>" -H "Content-Type: application/json" \
  -d '{"action":"windows"}'
```

用完把这串清空（`"debug_token": ""`）再重载插件，路由就不会注册。

---

## 排错

| 现象 | 原因与处理 |
| --- | --- |
| 模型说「手机不在线」 | ① AutoX.js 脚本没在跑；② 手机和电脑不在同一网络；③ 电脑防火墙拦了 8765；④ 手机休眠断网（加电池白名单 + 保持充电试试）。用手机浏览器开 `/healthz` 二分定位。 |
| 日志「❌ token 不匹配」(401) | 手机脚本 `TOKEN` 和插件配置不一致。 |
| 截图失败 | 没给「屏幕录制」权限。AutoX.js 里停止脚本重跑，重新允许。 |
| 截图很糊 | `screenshot_quality` 调高到 70–80，代价是图更大更慢。 |
| 输入框打不进字 | 先 `phone_tap` 点一下输入框再输入。仍失败说明该 App 屏蔽了无障碍（微信/银行常见），脚本会退到剪贴板粘贴。 |
| 点不动、点击落在别处 | 分辨率变了（换主题/横竖屏）。`phone_status` 看当前 `w×h`，坐标必须按真实像素。 |
| 手机锁屏了 | 无 root 解不了锁。保持屏幕常亮：设置 → 显示 → 休眠 → 永不（或充电时）。 |
| 插件日志报端口占用 | 改 `listen_port`。 |
| 后台一会就被杀 | AutoX.js + 电池白名单；小米还要开「自启动」和「后台弹出界面」。 |
| 说「已打开某应用」但屏幕没动 | ①`phone_status` 看 `overlay` 是不是 `true`，`false` 就去系统设置给 AutoX.js 开「显示在其他应用上层 / 悬浮窗」再重跑脚本；②vivo/iQOO 再开 i管家 → 权限管理 → 后台弹出界面；③微信这类应用会拒绝被后台唤起，脚本已带兜底（先把 AutoX 顶到前台再唤起），屏幕会闪一下 AutoX 属正常。 |
| `overlay_note` 里写 `floaty 失败` | 说明这台机器上 AutoX 的 `floaty` 模块用不了（已知问题），脚本会自动退回 `WindowManager`，只要最终 `overlay: true` 就不用管。 |

### 安全提醒

- `allowed_user_ids` 一定要填。留空时插件会在日志里告警，但那只是告警。
- token 别用能被猜到的字符串（比如手机号、`123456`），它是手机端唯一的凭据。
- 微信/支付宝/银行聊天和设置界面默认不截图（`sensitive_packages`）。
- 说「停下」会触发 `phone_stop`，清空队列里所有待执行任务。
