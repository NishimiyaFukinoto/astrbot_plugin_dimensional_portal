# 插件市场上架记录

> 记录「次元门」在 AstrBot 插件市场的上架流程、字段决定与本仓库必须保持的结构约束。
> 最后更新：2026-11-01

## 1. 现在到哪一步了

| 项 | 状态 |
| --- | --- |
| GitHub 仓库公开 + 结构合规 | ✅ `main.py` / `metadata.yaml` 在仓库根目录 |
| 版本 | `v0.4.1`（tag 已推，Release 附件由 CI 构建） |
| AstrBot Cloud 账号 | ✅ `nishimiyafukinoto`（GitHub OAuth 登录） |
| GitHub App 授权 | ✅ `AstrBot Cloud` App 已安装，仓库访问范围 All repositories |
| 提交审核 | ✅ 2026-11-01 提交 `v0.4.1`，状态 **Waiting / 等待安全检查** |
| 市场公开可见 | ⏳ 等审核通过后自动进入市场索引 |

提交后的后台地址（需要 Cloud 账号登录）：
<https://cloud.astrbot.app/plugin/NishimiyaFukinoto/astrbot_plugin_dimensional_portal?tab=versions>

## 2. 关键约束：仓库根目录必须就是插件目录

AstrBot 插件市场的工作方式是「**把 GitHub 仓库打成 zip 交给 AstrBot 安装**」，而 AstrBot 安装插件要求
zip 顶层目录里就有 `metadata.yaml`。所以：

```
仓库根目录
├── main.py              ← 必须有
├── metadata.yaml        ← 必须有（含 name/display_name/desc/version/author/repo）
├── _conf_schema.json
├── bridge/  safety/     ← 插件代码
├── phone/ docs/ tests/ tools/ assets/   ← 这些不会进 zip（打包白名单控制）
└── README.md  LICENSE  CHANGELOG.md
```

**踩过的坑**：最初插件本体放在 `plugin/` 子目录，市场发布页第 1 步「从 GitHub 仓库解析插件」
稳定返回 `502 origin_bad_gateway`（`POST /api/v1/market/plugins/parse/github`）。改成根目录布局后
同一次点击立刻解析成功。所以「解析 502」优先怀疑**仓库结构**，不是网络或 GitHub App 授权。

## 3. 字段决定（`metadata.yaml`）

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `name` | `astrbot_plugin_dimensional_portal` | 英文唯一标识，装进 `data/plugins/` 的目录名 |
| `display_name` | `次元门` | 市场里展示的名字 |
| `short_desc` | 用 QQ 里的 AI 远程操控安卓手机 | 紧凑 UI 只有一行，用这句 |
| `desc` | 多行 Markdown | 能做什么 / 怎么做的 / 安全 / 需要准备，市场详情页原样渲染 |
| `version` | `0.4.1` | 语义化版本；发新版必须改这里 |
| `author` | `西宫吹乃` | 显示作者，不是插件名 |
| `repo` | GitHub 地址 | 市场「仓库」按钮 |
| `category` | `效率` | 不填默认「其他」；填了市场解析会采用 |
| `astrbot_version` | `>=4.29.0` | 低于此版本的 AstrBot 不给装 |
| `support_platforms` | `[aiocqhttp]` | 只在这个通道真机验证过，不虚报 |
| `tags` | 远程控制 / 安卓自动化 / 截图 / AutoX.js / QQ | 参与搜索 |
| `social_link` | GitHub 主页 | 详情页外链 |

## 4. 发新版的固定流程

```bash
# 1. 改代码 + 跑测试（全绿才发）
python tests/smoke_test.py                       # 35
"$VENV" tests/live_server.py                     # 48（实例 venv）
"$VENV" tests/admin_console_test.py              # 58（实例 venv）
"$VENV" tests/import_check.py                    # 11 个工具
"$VENV" -m ruff check main.py bridge safety tests tools

# 2. 升版本 + 写 CHANGELOG
#    metadata.yaml 的 version、CHANGELOG.md 顶部加一节

# 3. 打 zip 自检（结构、体积）
python tools/make_release_zip.py

# 4. 提交 + 打 tag
git commit -am "..." && git push
git tag -a v0.4.2 -m "..." && git push origin v0.4.2

# 5. GitHub Release（带 tag，正文写清改了什么）
#    release 发布后 CI（release-asset.yml）自动构建并上传 zip

# 6. 回到 https://cloud.astrbot.app/publish 重新「解析 → 提交审核」
```

账号限额：**同时最多 3 个版本在审、滚动 24 小时最多提交 3 次**，别拿审核次数当测试。

## 5. 审核与更新机制

- 提交后状态是 `Waiting`（「已提交发布，等待安全检查」），通过后市场条目生效。
- 市场客户端读的是 `https://api.soulter.top/astrbot/plugins`，带 md5 缓存；
  条目更新后用户端要刷新插件市场（或等缓存过期）才看得到。
- 版本更新是「同一个插件下新增版本」，不需要重新提交整个插件。
- 撤销/回滚：后台版本页有「撤销版本」。

## 6. 出问题时先查什么

| 症状 | 先查 |
| --- | --- |
| 「解析」一直 502 | 仓库根目录是不是直接有 `metadata.yaml` + `main.py`（见第 2 节） |
| 解析出来字段缺 | `metadata.yaml` 的 YAML 是否合法（`yaml.safe_load` 验一遍） |
| 解析成功但审核被拒 | 后台版本页的 Reviewer Suggestion；多数是描述不合规或体积问题 |
| 市场搜不到 | 审核是否真的通过；客户端市场索引是否有 md5 缓存 |
| 装完启动报错 | zip 结构（必须是单层目录 + 根级 `metadata.yaml`）、依赖是否写在 `requirements.txt` |
