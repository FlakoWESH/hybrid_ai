# hybrid_ai · 群聊 AI 插件合集

两个配合使用的 zhenxun_bot / nonebot2 群聊 AI 插件：

| 插件 | 触发方式 | 职责 |
|---|---|---|
| [`hybrid_ai/`](hybrid_ai/) | 被 @ 才回（`to_me()`） | 语料库 + 青云客 + LLM 三层路由，短消息走 15k+ 关键词语料，长消息走大模型 |
| [`group_chatter/`](group_chatter/) | 不被 @ 也能按概率插话 | 旁听群聊，按概率随机接话，自动忽略其他命令和 bot |

## 部署

把两个子目录分别拷进 zhenxun 的 `ext_plugins/`：

```
ext_plugins/
├── hybrid_ai/        # @响应 AI
└── group_chatter/    # 随机插话
```

两个插件独立启停：

- `hybrid_ai`：群里 @ 机器人即可，私聊不回
- `group_chatter`：默认关闭，在 config.yml 把 `CHATTER_ENABLE` 设为 `true` 开启，`CHATTER_RATE` 控制插话频率

## 配合关系

- `hybrid_ai` priority=998（block=True）：被 @ 时接管回复
- `group_chatter` priority=999（block=False）：不挡其他插件，被 @ 时只旁听不插嘴

group_chatter 会自动检测并忽略 `on_command` / `on_alconna` 注册的命令，避免把其他 bot 的指令误当闲聊；`IGNORE_USER_IDS` 可填入其他 bot 的 QQ 号彻底排除。
