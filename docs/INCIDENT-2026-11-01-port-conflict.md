# 事故复盘：NapCat 连不上（Windows 保留端口吃掉了 6199）

> 时间：2026-11-01
> 影响：QQ 消息收发全断（NapCat 每秒重连报错），手机遥控插件本身正常
> 结论：**不是插件的问题**。AstrBot 的 `aiocqhttp` 适配器绑不上 6199，因为 6199 落在 Windows 的「保留端口段」里

## 1. 现象

- QQ 里收不到「大肥鱼」的任何回复，NapCat 日志刷屏 `websocket 连接失败`，大约每秒一次。
- `netstat -ano | findstr 6199`：**一条记录都没有**（既没有 LISTENING，也没有连接）。
- 但插件端口正常：`curl http://127.0.0.1:8765/healthz` → `{"ok": true, ...}`。
- AstrBot 面板能打开，说明核心本身是活的。

## 2. 时间线

| 时间 | 事件 |
| --- | --- |
| 2026-11-01 04:06 | 启动器把 AstrBot 从 **4.29.3 升级到 4.29.4**，`core/` 目录被整体重写 |
| 04:06 之后 | 核心重启，`aiocqhttp` 适配器第一次尝试绑 6199 → 失败 |
| 12:50:31 | AstrBot 日志出现 `[WinError 10013]`（见下） |
| 13:0x | 确认 `6199` 在 Windows 保留端口段 `6159-6258` 内 |
| 13:02 | 换端口：AstrBot → **16100**，NapCat → **16100**，重启后恢复 |

> 关键点：**换端口之前一直没炸，是因为上一次开机时 HNS 预留的段没盖到 6199**。
> 每次开机预留的段是重新分配的，所以这属于「早晚会撞上一次」的定时炸弹。

## 3. 根因

### 3.1 这台机器的动态端口范围被改过

```
> netsh int ipv4 show dynamicport tcp
协议 tcp 动态端口范围
启动端口        : 1024
端口数          : 13977          → 动态范围 = 1024 – 15000
```

Windows 默认是 **49152–65535**，这台机器是 **1024–15000**（被 Docker Desktop / WSL2 / 某些"优化工具"改过）。
动态范围越小、越靠下，就越容易和普通服务端口（几百到几万）打架。

### 3.2 Hyper-V 的网络组件会在动态范围里预留端口段

机器上 `vmcompute` / `hns` / `winnat` 三个服务都在跑（Hyper-V / WSL2 / 容器网络）。

HNS 会在动态端口范围内「预留」若干段给自己，预留结果就是：

```
> netsh int ipv4 show excludedportrange protocol=tcp
开始端口    结束端口
      1268        1367
      5141        5240
      5959        6058
      6059        6158
      6159        6258      ← 6199 在这里面
      6259        6358
      6469        6568
      6589        6688
```

**被保留的端口，任何进程 `bind()` 都会直接失败**，错误码就是 `WSAEACCES (10013)`。

### 3.3 AstrBot 的报错刚好对得上

面板 `/api/log-history`（`core/astrbot/dashboard/api/logs.py:176`）里：

```
[Core] [ERRO] [v4.29.4] [platform.manager:278]: ------- Task platform_aiocqhttp_报错 failed: [WinError 10013] ...
    await inst.run() → await worker_serve(...) → sockets = config.create_sockets()
    → insecure_sockets = self._create_sockets(self.bind) → sock.bind(binding)
[Core] [ERRO] [v4.29.4] [platform.manager:280]: |    PermissionError: [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试
```

`aiocqhttp` 适配器读的是 `cmd_config.json` 的 `platform[0].ws_reverse_port`
（`core/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py:45-46`，
`run()` 里 `CQHttp.run(host=..., port=int(self.port), ...)`，底层 hypercorn 直接 `sock.bind`）。

## 4. 链路方向（顺带澄清）

这条链路是**反向 WebSocket**：

```
NapCat（客户端）  ──连接──▶  AstrBot aiocqhttp（服务端，监听 6199/16100）
```

所以「监听端口」在 **AstrBot** 这边，NapCat 只负责往外连。
NapCat 连接地址配置在 `.../napcat/config/onebot11_<QQ号>.json` 的 `network.websocketClients[0].url`。

## 5. 处置

1. **AstrBot 侧**：`core/data/cmd_config.json` → `platform[0].ws_reverse_port` = `6199` → **`16100`**（也可在 WebUI 的平台配置里改）。
2. **NapCat 侧**：`onebot11_2108787342.json` → `network.websocketClients[0].url` = `ws://127.0.0.1:6199/ws` → **`ws://127.0.0.1:16100/ws`**。
   - 改之前先备份：`onebot11_2108787342.json.bak-20261101-130207`
   - **必须在 NapCat / QQ 退出时改**，否则它退出时会把旧配置写回去。
   - `token` 和 `enable` 不用动。
3. 两边都重启。

## 6. 验证

```bash
# AstrBot 在听 16100
netstat -ano | findstr 16100          # → 127.0.0.1:16100  LISTENING  <AstrBot PID>

# NapCat 连上之后会多一条 ESTABLISHED
netstat -ano | findstr 16100          # → 127.0.0.1:16100  127.0.0.1:xxxxx  ESTABLISHED

# 插件不受影响
curl -H "X-Token: <token>" http://127.0.0.1:8765/healthz   # → "online": true
```

## 7. 遗留风险：8765 也在动态范围里

| 端口 | 是否在 1024–15000 | 风险 |
| --- | --- | --- |
| 6199（原） | 是 | 已被吃掉 |
| 16100（现） | **否**（>15000） | 安全 |
| 8765（插件） | **是** | 某次开机可能被同样的方式吃掉 |

插件端口如果被吃掉，表现是插件启动报 `[WinError 10013]`、QQ 里 `phone_*` 全部失败。

## 8. 长期方案（三选一）

**A. 把动态端口范围改回默认（推荐，一次到位）**

管理员 PowerShell：

```powershell
netsh int ipv4 set dynamicport tcp start=49152 num=16384
netsh int ipv4 set dynamicport udp start=49152 num=16384
```

重启后 HNS 只会去 49152 以上抢，`8765` / `16100` 这类端口就彻底安全了。

**B. 把所有要用的端口挪到 15000 以上**

例如插件从 `8765` 换到 `18765`：插件配置 `listen_port` + 手机脚本 `BASES` 里的端口一起改。

**C. 每次开机查一眼（治标）**

```powershell
netsh int ipv4 show excludedportrange protocol=tcp
```

看自己要用的端口在不在里面；在的话 `net stop winnat && net start winnat` 让它重新分配（不保证变好）。

## 9. 学到的东西（已写进 AGENTS.md 踩坑表）

1. **端口绑不上但 `netstat` 没记录**，先查 `excludedportrange`，别怀疑代码。
2. **Windows 的动态端口范围可能被改小**，`show dynamicport tcp` 是排查第一步。
3. **反向 WS 要改两边**：AstrBot 的 `ws_reverse_port` + NapCat 的 `url`，少改一边就是「连不上」。
4. **NapCat 退出会回写配置**，改它的 json 必须趁它没运行。
