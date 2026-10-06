# 网页聊天采集的边界

`extensions/browser` 是 Manifest V3 扩展，目标 Chrome / Edge / Brave。仅匹配 chatgpt.com、claude.ai、gemini.google.com，不读取普通网页、Cookie、认证头或浏览器完整历史。

## 新消息判定

初始 DOM 是基线。页面里后来出现更多历史节点也只是基线补充。只有观察到 `event.isTrusted` 的发送操作，并在之后的新用户消息节点中匹配到同一段问题，才记录这次问题和其后的新回复。消息创建时间标记为 live_observed，不冒充服务器 create_time。

输入事件可信只表示浏览器真的发生了交互，不能证明这是人的物理按键；自动化驱动、无障碍输入和粘贴仍然可能存在。因此用户消息归为用户侧指令而非已证实本人打字。

流式文字更新复用同一 ID 和最初观察时间，不当作多条回答。只有稳定 conversation 路径出现后才捕获新对话，避免临时首页 ID 重复。消息没有稳定平台 ID 时只能使用当前 DOM 实例的临时 ID，重挂载和站点改版需要真实浏览器验证。

## 本地队列

后台 service worker 将事件先写入 chrome.storage.local。并发标签页串行合并，暂时无法连接本机时每分钟重试。队列最多 2000 条且控制在约 7 MiB；溢出会保留现有消息并显示提示。token 仅存扩展本地存储，不注入页面。

所有数据请求仅发向 localhost / 127.0.0.1。配对 token 由本机服务生成。更换端口后需同步扩展地址。

## 已知未覆盖

语音、附件独立发送、某些带修饰键的输入模式、编辑旧问题、重新生成回答、站点原生工作区的特殊 DOM，以及超出 30 分钟的单次回答，可能无法准确捕获。这些不静默推算。实时扩展从安装并启用后开始工作，不能回溯此前关闭期间的完整网页活动。

需要历史消息源时间时使用 `workledger import conversations.json --format chatgpt`。导入 current_node 所在分支，保留其他分支为非今日证据；以每条消息 create_time 而非 conversation.update_time 归日。

控制面板的“暂停后台扫描”与扩展自身开关分开。暂停扫描后，Agent 原生日志仍由 Agent 自己写入，浏览器扩展也可能继续排队。需要停止网页采集时关闭扩展中的“启用采集”。

## 验证状态

11 个 tracker 状态机测试已经运行。真实 app.js 的离线渲染与表单 payload 已做检查。开发容器的 Chromium 管理策略禁止 URL 导航和扩展安装，完整 `tests/ui_smoke.py` 因此没有运行成功；没有修改该策略。实机应在用户正常允许的浏览器环境验收。Safari Web Extension 转换、签名和安装未实现。
