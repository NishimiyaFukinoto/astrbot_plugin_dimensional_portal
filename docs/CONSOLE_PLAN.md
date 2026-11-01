# 管理控制台 · 执行方案

> 状态：**V1 已上线；V1.2 视觉重做（明色·樱空）已完成**；V2 功能（安全策略开关、审计列表）待做。

## 实现现状

| 部分 | 状态 |
| --- | --- |
| `/console?k=` + 302 本机免密钥 | ✅ 已上线 |
| `/admin/state` `/admin/config` `/admin/stop` | ✅ 已上线 |
| 设备状态卡 / QQ 白名单增删 / 急停 | ✅ 已上线 |
| 视觉 V1.2：**明色·樱空**（真实照片背景 + 白色玻璃卡） | ✅ 已上线 |
| `GET /console-assets/<name>` 背景图（白名单两个文件名） | ✅ 已上线 |
| 安全策略开关（敏感截图、写操作、限流、超时） | ⬜ V2，需要扩 `/admin/config` |
| 审计日志列表 | ⬜ V2，`/admin/state` 已带 `audit` 尾巴 |

### V1.2 视觉要点（`plugin/bridge/console.html` + `plugin/bridge/assets/`）

- 背景：Unsplash 实拍（Unsplash License）——桌面用横构图「樱花与晴空」`bg-wide.jpg`（1600×1067，205 KB），
  窄屏（`@media (max-aspect-ratio: 3/4)`）换竖构图「蓝天下的樱树」`bg-tall.jpg`（1000×1333，154 KB）；
  上面压一层 30%→90% 的白色渐变纱罩，保证深色文字在任何位置都够对比度。
- 卡片：白色玻璃（`rgba(255,255,255,.66)` + `backdrop-filter: blur(18px)`）、柔和紫色投影、顶部 hairline 光带。
- 标题：深玫瑰→靛紫渐变字，外挂白色描边辉光（在亮天空上也清楚）；副标题做成半透明白胶囊。
- 状态球：在线翡翠绿呼吸 + 涟漪，离线珊瑚红；电量用蓝→粉渐变条。
- 花瓣：淡粉白 CSS 花瓣（12 片，17–27s），`prefers-reduced-motion` 下整层隐藏。
- 轮询做了 diff：白名单没变就不重绘，避免每 5 秒重播一次入场动画。

### 背景图为什么要单独开路由

控制台的 CSP 是 `default-src 'none'; style-src/script-src 'unsafe-inline'; connect-src 'self'`，
原来连图片都被挡住。V1.2 加了 `img-src 'self'`，并新增 `GET /console-assets/{name}`：
只认 `CONSOLE_ASSETS = ("bg-wide.jpg", "bg-tall.jpg")` 这两个文件名（`web.FileResponse` + `Cache-Control: public, max-age=86400`），
其它名字一律 404，路径穿越拿不到源码。这两个图不带 token 也能取——它们只是装饰，不含任何设备信息。

## 0. 目标

在**不装任何新软件**的前提下，给这个插件一个能点两下的界面，用来：

1. 看设备状态（在线没、电量、前台应用）
2. **增删 QQ 白名单**（改完立刻生效，不用重载插件）
3. 开关安全策略（限流、敏感界面能否截图、任务超时）
4. 看审计日志、一键急停

## 1. 为什么是「插件内置网页」而不是独立客户端

| 形态 | 要用户做什么 | 能干的活 | 结论 |
| --- | --- | --- | --- |
| 桌面客户端（Electron/Tauri） | 装包、配地址、跟着更新 | 和网页一样 | ❌ 包大、要维护两套 UI |
| 手机 App | 装 APK、连 Tailscale | 和网页一样 | ❌ 更重，还要过权限 |
| 命令行脚本 | 会开终端、会传参 | 全都能 | ⚠️ 用户明确说过「不会弄」 |
| **插件内置网页** | **浏览器打开一个链接** | **全都能** | ✅ 零安装、手机也能开 |

插件本来就监听着 8765，加几个路由和一张静态页就够了。

## 2. 四条关键决策

### 2.1 白名单在配置里怎么存

- 选定：**字符串** `"123456789,987654321"`，用逗号分隔
- 理由：`_conf_schema.json` 里这一项声明的是 `string`。存 JSON 数组可能被 AstrBot 的配置校验当成类型错误，甚至在下次加载时被重置。
- 页面上仍然显示成一个一个可删除的小标签，读写时 `split(",")` / `join(",")`，用户看不出差别。

### 2.2 改完怎么生效

- 选定：**内存改 + `save_config()` 落盘 + 就地重建 `SafetyPolicy`**
  ```python
  self.config["allowed_user_ids"] = ",".join(ids)
  self.config.save_config()          # AstrBotConfig.save_config()
  self.policy = SafetyPolicy(...)    # 用新配置重建，不重启插件
  ```
- 否决方案：改磁盘文件 + 调面板接口重载插件。
  踩过的坑：AstrBot 退出时会把内存里的旧配置写回文件，手改的内容会被覆盖（`cmd_config.json` 就这样被还原过一次）。
- 依据：`astrbot/core/config/astrbot_config.py` 的 `AstrBotConfig.save_config()`，插件拿到的 `self.config` 就是它的实例（`star_manager.py` 加载时构造）。

### 2.3 入口怎么鉴权

- 选定：**自动生成的随机 `admin_token`**
  - 首次启动时生成，写进插件配置，并打印到 AstrBot 日志：
    `[dimensional-portal] 控制台：http://<本机IP>:8765/console?k=<admin_token>`
  - 之后可以在插件配置里改成任何字符串；填 `off` 则彻底关闭控制台
- 备选（未采用）：自设固定密码（好记但可被猜）、只允许 `127.0.0.1`（最安全但人必须在电脑前）

### 2.4 范围

- 做：设备状态、白名单、限流、敏感界面截图开关、任务超时、审计日志、急停、debug 口的开关状态
- **不做**（需要重启才生效的项，做了只会让人困惑）：监听地址/端口、手机端 `token`

## 3. 数据流

```
浏览器  ──GET /console──────────────▶  插件返回 console.html（内嵌 CSS/JS，无 CDN）
        ──GET /admin/state  (k)────▶  设备快照 + 当前配置 + 最近审计
        ──POST /admin/config (k)───▶  校验字段 → 改 self.config → save_config() → 重建 policy
        ──POST /admin/stop   (k)───▶  hub.emergency_clear()
```

页面每 5 秒轮询一次 `/admin/state`（不做 WebSocket，够用且简单）。

## 4. 接口设计

### `GET /console?k=<admin_token>`
返回 `console.html`（单文件、纯原生 JS、无外部依赖）。`k` 错误时返回 403，不返回页面。

### `GET /admin/state`
```json
{
  "ok": true,
  "online": true,
  "device": {"device": "vivo V2171A", "w": 1080, "h": 2400, "battery": 79,
             "charging": true, "cur_pkg": "com.android.settings",
             "accessibility": true, "screen_capture": true, "overlay": true},
  "config": {"allowed_user_ids": ["123456789", "987654321"],
             "rate_limit_per_minute": 20, "allow_screenshot_sensitive": false,
             "task_timeout_ms": 20000, "debug_enabled": false},
  "audit": [{"ts": "23:10:02", "kind": "task", "user": "123456789", "action": "screen"}],
  "queue": 0,
  "uptime_s": 3600
}
```

### `POST /admin/config`
```json
{"allowed_user_ids": ["123456789"], "rate_limit_per_minute": 30,
 "allow_screenshot_sensitive": false, "task_timeout_ms": 20000}
```
只接受白名单里列出的字段，其它字段一律忽略（防止误改 token/端口）。
返回 `{"ok": true, "applied": {...}}`，其中 `applied` 是**落盘后回读**的真实值。

### `POST /admin/stop`
清空任务队列，返回 `{"ok": true, "cleared": 3}`。

鉴权：三个 `/admin/*` 都校验 `X-Admin-Token` 头或 `?k=` 参数，错误 401。

## 5. 页面线框

```
┌───────────────────────────────────────────┐
│ 次元门                ● 在线  电量 79% │
│ vivo V2171A · 1080×2400 · 前台 com.…     │
├───────────────────────────────────────────┤
│ QQ 白名单                                  │
│  [123456789 ×] [987654321 ×]              │
│  [ 输入 QQ 号后回车添加…              ]    │
│  ⚠️ 至少保留一个号码                        │
├───────────────────────────────────────────┤
│ 安全策略                                   │
│  限流         [ 20 ] 次/分钟               │
│  敏感界面截图 [ 关 ]                       │
│  任务超时     [ 20000 ] 毫秒               │
├───────────────────────────────────────────┤
│ 审计日志（最近 50 条）        [ 急停 ]     │
│  23:10:02  123456789  screen               │
│  23:09:41  987654321  ui                   │
└───────────────────────────────────────────┘
```

## 6. 实施步骤（每步都能停下来验收）

| 步骤 | 内容 | 验收标准 |
| --- | --- | --- |
| 1 | `server.py` 加 `/console`、`/admin/state`（只读） | 浏览器能打开页面，看到实时设备状态 |
| 2 | `main.py` 加配置应用逻辑 + `save_config()`；`/admin/config` 生效 | 页面加一个 QQ 号 → 该号发消息立刻能通，不重载 |
| 3 | 补齐页面：白名单增删、开关、审计、急停 | 手机浏览器打开也能正常用 |
| 4 | `tests/admin_console_test.py` + 文档 + 部署 | 测试全绿；部署到 AstrBot 后重启一次即可见 |

## 7. 安全设计

| 项目 | 做法 |
| --- | --- |
| 谁能改配置 | 只有持有 `admin_token` 的人；手机端 `token` 只能取任务，无权管配置 |
| 防爆破 | 同一 IP 每分钟最多 10 次失败，超了锁 60 秒 |
| 误删锁死 | 保存时校验「白名单至少留一个号」，不允许清空 |
| 可追溯 | 每次配置改动写一行审计（`kind=admin`，含来源 IP 与改动内容） |
| 一键关闭 | 配置里 `admin_token` 填 `off`，控制台路由直接不注册 |

## 8. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 页面被 Tailscale 网段外的人扫到 | 所有接口都校验 `admin_token`；URL 不带 token 一律 401 |
| 保存了但没真正落盘 | 保存后立刻回读 `self.config` 再返回给页面，页面显示的就是内存真值 |
| 并发写配置冲突 | 改动走单进程内的一次性操作；`AstrBotConfig` 自带写锁 |
| 页面依赖 CDN 导致断网打不开 | 单文件内嵌 CSS/JS，零外部请求 |

## 9. 待确认

1. `admin_token` 用自动生成的随机串，还是你想自己设一个好记的？
2. 控制台要不要支持手机浏览器访问（走 Tailscale 内网地址）？
3. 范围按「全套（除需重启项）」做，还是先只做「白名单 + 状态 + 急停」？
4. 以后要不要也支持在 QQ 里用嘴改白名单（需要模型调工具，多一层，容易出错）？

## 10. 明确不在本次范围

- M3 相机 / M4 装 APK / M5 保活
- 多设备管理（同一插件对接多台手机）
- 权限系统（多用户、角色区分）
