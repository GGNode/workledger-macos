# 架构与事件语义

```
Agent 原生日志 / OpenCode 只读 DB / 可选 hooks
项目目录快照 / VS Code 保存事件 / 浏览器新消息
                         ↓
                  规范化事件 + 来源证据
                         ↓
          SQLite WAL：事件、会话、修订、人工确认
                         ↓
         按本机时区选当天 → 项目分组 → 主任务汇总
                         ↓
       简短 HTML / Markdown + 折叠证据 + 可选模型整理
```

## 数据模型

`events` 保留 source、native_id、kind、actor、session_id、occurred_at、observed_at、ended_at、chronology、artifact、text、metadata、fingerprint。稳定 ID 基于来源、原生身份和事件族；反复读取同一条记录不增加事件，内容修订进入 revisions。用户归属确认进入 append-only attribution，不覆盖原始事件。`sessions` 保存 parent_id 与关系类型。显式人工指定的 session link 不被弱原生继承字段覆盖。

`source_time` 使用原始可信格式的消息时间；`live_observed` 是本机确实观察到新事件的时刻；`historical` 与 `unknown` 不参与当天成果统计。历史内容的首次发现时间不会自动变成创建时间。

一个文件的保存快照不代表每一行归属。报告明确区分成功工具结算所证明的 Agent 写入、本人确认、待确认变化。Agent 自然语言输出用“Agent 报告”展示，不升级成独立验证的实验结论。

## 并发与持久化

后台线程、手动命令、HTTP 处理线程各自打开 SQLite 连接；WAL 和 30 秒 busy timeout 处理并发。采集和报告生成使用 `flock`，避免两次扫描互相覆盖快照。单个源导入有 savepoint，错误会回滚该源；一个不可用来源不阻断其他来源。更高版本数据库拒绝加载。

只读源数据库不使用 `immutable=1`，避免丢失正在 WAL 中的新消息。原始 Agent 日志只读。动态文件读取前后验证 stat 稳定性；首次观察为基线，之后新增、内容变化、删除/移走作为未知作者的观察事件。

## 时间统计

只统计有明确起止记录的执行区间，并裁剪到本地报告日的两个午夜，支持夏令时的 23/25 小时天。区间并集是已覆盖墙钟时间；先按每个会话求并集再求和是会话区间总量。它们包含等待，不称为 CPU 计算工时，更不算本人的工作时长。缺少起止事件时不凭两条消息之间的间隔造工时。

## 本机接口

服务只绑定 `127.0.0.1`。网页壳可加载，数据接口要求 token；检查 Host 和 Origin，拒绝外站 Origin 和 DNS rebinding Host。token 通过 URL fragment 交给控制面板，再从地址栏移除，API 使用 Authorization。CORS 允许受支持扩展来源，但仍要求 token。HTTP 输入有大小限制，模型响应有大小上限。

采集数据是不可执行的输入。模型只接收筛选后的短事实卡片，不能调用工具；报告中的 HTML 均转义。来源中的命令不会在本机运行。远程模型需显式 allow_remote 且必须 HTTPS，不允许隐式重定向。配对 token 与 SQLite 默认权限 0600。模式匹配脱敏只覆盖常见凭证，不能替代项目路径排除和本机访问控制。

## 调度

launchd 启动一个持续采集进程。进程自己检查本地时间和自动报告设置，按日期记录是否已生成；睡眠后最多补三天，缺失时段只反映已有证据。手动运行不会禁用之后的自动生成。自动报告默认关闭。后台暂停是暂停扫描，恢复后仍可补读原生 Agent 已保存的活动。
