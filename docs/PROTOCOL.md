# 通信协议 v1

电脑（插件）和手机（AutoX.js 脚本）之间的全部约定。改任何一边都要同步这里和
`plugin/bridge/protocol.py`、`phone/excalibur_agent.js` 里的 `AGENT_VERSION` / `PROTOCOL_VERSION`。

## 角色

- **服务端**：插件里的 aiohttp，默认 `0.0.0.0:8765`。
- **客户端**：手机 AutoX.js 脚本。手机永远主动发请求，电脑从不主动连手机。

## 鉴权

所有端点（除 `/healthz`）都要求请求头 `X-Token: <token>`，也接受 `?token=<token>`。
token 在插件配置里设置，手机脚本顶部 `TOKEN` 必须一致。用 `hmac.compare_digest` 比较，不匹配回 `401`。

## 端点

### `POST /hb` — 心跳（手机每 10 秒一次）

请求体：

```json
{
  "device_id": "a1b2c3d4e5f6a7b8",
  "device": "Xiaomi 23127PN0CC",
  "android": "15",
  "w": 1080, "h": 2400,
  "battery": 87,
  "charging": true,
  "screen_on": true,
  "cur_pkg": "com.android.launcher3",
  "accessibility": true,
  "screen_capture": true,
  "overlay": true,
  "overlay_note": "ok(WindowManager)",
  "agent_version": "0.2.0",
  "protocol": 1
}
```

响应：

```json
{"ok": true, "server_ts": 1761..., "server_seq": 7, "queue": 0, "poll_interval_ms": 1000, "protocol": 1}
```

服务端超过 `offline_after_seconds`（默认 40s）没收到心跳就算离线，此时下发任务直接抛
`DeviceOffline`，模型会看到「手机不在线」的提示。

### `GET /poll?since=<server_seq>&wait=<秒>` — 长轮询取任务

- `wait` 上限 30s，手机端用 12s（比服务端离线阈值小，保证心跳够频繁）。
- 有任务立刻返回，没有就挂起到超时。

响应：

```json
{
  "ok": true,
  "server_seq": 8,
  "jobs": [
    {"id": "t_1f2e3d4c5b6a", "action": "tap", "params": {"x": 540, "y": 1200},
     "timeout_ms": 8000, "created_at": 12345.678}
  ]
}
```

`jobs` 里可能一次返回多个（队列里堆了多个任务时）。手机端拿到后必须把 `server_seq`
记成下一次的 `since`，否则会重复取到任务。

```json
{"ok": true, "server_seq": 8, "jobs": [], "timeout": true}
```

### `POST /result` — 回传结果

```json
{"id": "t_1f2e3d4c5b6a", "ok": true, "data": {"pkg": "com.android.settings"}, "error": null,
 "finished_at": 1761...}
```

响应 `{"ok": true, "accepted": true}`。`accepted: false` 表示任务已超时或未知，结果被丢弃（正常现象，不用重试）。

失败时 `ok: false`，`error` 是给模型看的中文原因（如「没找到可输入的焦点输入框」）。

### `GET /healthz` — 存活探测（免 token）

```json
{"ok": true, "service": "excalibur-phone-bridge", "online": true, "queue": 0, "uptime_s": 123.4, "ts": 1761...}
```

手机浏览器打开 `http://电脑IP:8765/healthz` 能看到这个，就说明网络通了。

### `POST /debug/submit` — 调试下发（默认不注册）

只有插件配置里 `debug_token` 非空时才会注册，且请求头必须带匹配的 `X-Debug-Token`。
用来做自动化回归：直接下发动作、同步拿结果，不经过模型，也不受白名单/限流影响。

请求：`{"action": "status", "params": {}, "timeout_ms": 20000}`
响应：成功 `{"ok": true, "data": {...}}`；失败 `{"ok": false, "error": "..."}`。

**当前 `debug_token` 是空值，这个端点没有注册**（`POST /debug/submit` 返回 404）。要用时临时填一个随机串并重载插件，用完清空——否则等于开了一个绕过白名单的口子。

## 任务体

```json
{"id": "t_xxxxxxxxxxxx", "action": "tap", "params": {...}, "timeout_ms": 8000, "created_at": 12345.678}
```

`timeout_ms` 是服务端等待结果的超时；插件实际等 `timeout_ms + 3s`（给手机执行留余量）。
排队超过 90 秒还没被取走的任务会被服务端丢弃，避免手机重连后执行一堆陈旧操作。

## 动作表

| action | params | data（成功回传） |
| --- | --- | --- |
| `screen` | `quality`(1-95) | `image_b64`(JPG,无换行), `w`, `h`, `pkg`, `format`, `quality` |
| `ui` | `max_nodes` | `pkg`, `w`, `h`, `win`, `nodes[]`, `popup_maybe` |
| `tap` | `x`, `y` | `clicked`, `ok`, `pkg` |
| `long_press` | `x`, `y`, `duration` | `pressed`, `duration`, `pkg` |
| `swipe` | `x1`,`y1`,`x2`,`y2`,`duration` | `swiped`, `duration`, `pkg` |
| `text` | `text`, `submit` | `method`(`setText`/`paste`), `submitted`, `pkg` |
| `key` | `name` | `key`, `ok`, `pkg` |
| `open_app` | `package` 或 `name` | `launched`, `pkg`, `target`, `bounced`, `warning`, `seen` |
| `find_text` | `text`, `index`, `exact`, `tap` | `found`, `matched`, `cx`, `cy`, `tapped`, `total`；没找到时 `found:false` + `texts[]` |
| `wait` | `ms` | `waited_ms`, `pkg` |
| `status` | — | 同心跳体 |
| `windows` | — | 诊断用：`windows[]{pkg,type,focused,active,order,score,has_root,layer,bounds,children}`, `pkg` |
| `camera_photo` / `camera_video` / `download_apk` / `install_apk` | — | **未实现**，回 `ok:false`（M3/M4） |

`key` 支持：`back` `home` `recents` `enter` `delete` `volume_up` `volume_down`。

实现说明（重要，别踩）：

- `back/home/recents/volume_*` 走自动化的全局动作，可靠。
- **无障碍 API 无法注入任意按键事件**（没 root 时），所以：
  - `delete` = 把焦点输入框的文字清空（不是真的按删除键）；
  - `enter` = 找「搜索/发送/确定/完成」按钮点掉。找不到就报错，不假装成功。
  - `text(submit=true)` 同理走「找提交按钮」。
- 输入优先 `ACTION_SET_TEXT`（不需要输入法），不行再退剪贴板粘贴。

### `open_app` 的返回怎么读

```json
{"launched": true, "pkg": "com.tencent.mm", "target": "com.tencent.mm",
 "bounced": false, "warning": "", "seen": null}
```

- `launched=false`：系统不接受启动请求——包名不对或没装。
- `bounced=true`：走了「先把 AutoX 顶到前台再唤起」的兜底路径（屏幕会闪一下 AutoX）。
- `warning` 非空 = 启动请求发出去了但前台没变，文案会说明是「被 vivo 应用跳转框拦住」还是
  「应用拒绝被唤起 / 后台启动限制」，并给出处理建议。
- `seen`：失败那一刻的界面快照（`pkg` / `win` / `popup_maybe` / 前 15 条文字），用来回答「当时屏幕上到底是什么」。

**为什么后台能打开应用**：Android 10+ 禁止后台应用启动 Activity，官方豁免之一是「有悬浮窗权限且当前有可见窗口」。
所以脚本启动时用 `WindowManager` 加了一个 1×1、`alpha=0.01`、不可点不可聚焦的 `TYPE_APPLICATION_OVERLAY` 窗口。
`status.overlay` 就是这个守卫的状态，`overlay_note` 是创建失败的原因。

### 前台窗口怎么判定

`getWindows()` 里常年混着输入法（type=2）和系统浮层（type=3，例如 vivo 的 `com.vivo.upslide`），
所以「当前界面」不是取第一个窗口，而是按分数排序：

```
输入法窗口         → -1000（直接排除）
focused            → +200
active             → +60
type == 应用窗口(1) → +50
type == 系统窗口(3) → -50
拿不到 root 的窗口  → -400
最后 - 窗口顺序（同分时靠前的优先）
```

`ui` / `status` / `find_text` 全部用这个结果；`windows` 动作能把每个窗口的分数打出来，排查「取错窗口」很方便。

### UI 节点结构

```json
{"cls": "android.widget.TextView", "text": "设置", "desc": "", "id": "title", "depth": 4,
 "clickable": true, "editable": false, "selected": false, "enabled": true,
 "left": 0, "top": 96, "right": 1080, "bottom": 240,
 "cx": 540, "cy": 168}
```

`cx/cy` 是中心坐标，插件渲染成 `@(540,168)` 给模型直接拿去 `phone_tap`。

采集规则（都是踩过坑之后定的）：

- **广度优先（BFS）**：长按菜单、弹窗这类浮层节点天生「浅层、排在后半段」，
  DFS 会一头扎进背景列表，`max_nodes` 一截断就永远照不到弹窗。
- **只输出有信息量的节点**（有文字 / 有描述 / 可点 / 可输入 / 选中），纯容器（空 FrameLayout）跳过，
  否则 `max_nodes` 全被空壳吃掉。
- `visible=false`、宽高为 0 的节点直接过滤。
- `ui` 还会返回 `win`（选中窗口的 pkg/type/focused/active）和 `popup_maybe`
  （存在「拿不到 root 的非全屏窗口」时给出提示——说明屏幕上有一层读不到的东西，改用截图）。

## 安全边界

- 白名单之外的用户调用任何 `phone_*` 都被拒，并写审计日志。
- 每用户每分钟最多 20 次下发（可配）。
- 敏感应用（微信、支付宝、银行、设置等）默认**不截图**，只允许读 UI 文字。
- `package_blacklist` 里的包名禁止 `open_app`；M4 的安装动作也要过这一关。
- 审计日志写在 AstrBot 的 `data/plugin_data/astrbot_plugin_excalibur/audit.jsonl`
  （旧版本没有 `get_astrbot_plugin_data_path` 时退回插件目录下的 `data/`），字段
  `{"ts","kind","user","action","params",...}`，`kind` 有 `task` / `deny_user` / `deny_rate` / `emergency_stop`。
