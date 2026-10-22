# hybrid_ai 设计文档：技术痛点与逻辑冲突分析

> 配套文档：README.md
> 本文聚焦"为什么这样设计"——把两个异代插件缝合时遇到的架构决策、技术痛点和逻辑冲突全部摊开。

---

## 一、青云客 API 实测报告（2026-10-21）

### 1.1 官方接口规格

来源：官网 `http://api.qingyunke.com/`

| 项目 | 内容 |
| --- | --- |
| 请求地址 | `http://api.qingyunke.com/api.php` |
| 请求方式 | GET，utf-8 |
| 鉴权 | 无需 key，固定 `key=free&appid=0` |
| 参数 | `key=free`、`appid=0`、`msg=<urlencode文本>` |
| 返回 | `{"result":0,"content":"..."}` |
| 频率限制 | 官方要求 ≤200 次/10 分钟 |
| 特殊标记 | `{br}` 换行；返回自称"菲菲" |

### 1.2 实测结果矩阵

用官网文档中列出的功能关键词逐一实测（非插件内置测试词）：

| 测试类型 | 测试词 | 结果 | 实际返回摘要 |
| --- | --- | --- | --- |
| 普通聊天 | 今天天气怎么样 | ✅ 正常 | "先告诉菲菲您在哪个城市…" |
| 普通聊天 | 吃饭了吗 | ✅ 正常 | "还没吃呢，你请客吗？" |
| 普通聊天 | 你好 | ✅ 正常 | "不怎么好" |
| 普通聊天 | 在吗 | ✅ 正常 | "哟，我天天都在啊" |
| 普通聊天 | 嗯 | ✅ 正常 | "...嗯" |
| 普通聊天 | 哦 | ✅ 正常 | "明白的意思" |
| 普通聊天 | 你是谁 | ✅ 正常 | "我是一个小笨蛋啊" |
| 普通聊天 | 再见 | ✅ 正常 | "拜拜~" |
| 计算 | 1+1*2/3-4 | ✅ 正常 | "结果：3.3333" |
| 成语 | 成语一心一意 | ✅ 正常 | 完整成语释义 |
| 笑话 | 笑话 | ⚠️ 内容低俗 | 返回超长文本，含大量不适合群聊内容 |
| 笑话 | 讲个笑话 | ⚠️ 内容低俗 | 同上 |
| 天气 | 天气北京 | ❌ 服务端报错 | 返回 PHP Warning `gzdecode(): data error` + "天气预报查询失败" |
| 翻译 | 翻译 good morning | ⚠️ 首次超时 | 第一次请求超时，功能不稳定 |
| 敏感请求 | 讲个黄色笑话 | ✅ 有拦截 | "菲菲不会讲黄色笑话啦" |
| 粗口语气 | 我爱你 | ⚠️ 语气粗 | "死一边去" |
| 粗口语气 | 哈哈 | ⚠️ 语气粗 | "哈个屁" |
| 连续 8 次调用 | 混合 | ✅ 8/8 成功 | 核心聊天功能稳定性可接受 |

### 1.3 结论

**青云客 API 可用，但需要"捆着用"：**

1. 核心自由聊天功能在线、可用、成功率高。
2. 笑话、天气、翻译等附加功能要么低俗、要么报错、要么不稳定，应在插件中通过关键词黑名单绕开。
3. 回复语气随性、偶有粗口，必须做本地内容清洗。
4. 必须加本地限流（比官方限制更保守）。

---

## 二、模块架构

```
┌─────────────────────────────────────────────────────────┐
│                      __init__.py                         │
│  on_message(rule=to_me() & group_only, priority=998,    │
│             block=True)   ← 仅群聊，私聊 rule 层即拦截   │
│  · 提取文本/图片  · handler 内 session.group 二次校验   │
│  · 调用主路由 route_message()  · reply_to 发送           │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    data_source.py                        │
│                    route_message()                       │
│                                                          │
│  复读检测 → 打招呼 → 图片 → 短消息分支 → 长消息分支       │
│  概率门控：ANIME_PROBABILITY / QYK_PROBABILITY            │
│  降级编排：LLM → 青云客 → no_result                       │
└──────┬───────────────┬──────────────┬───────────────────┘
       │               │              │
       ▼               ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│message_manager│ │ anime_store  │ │  qyk_client  │
│  复读/学舌检测 │ │ anime.json   │ │  青云客调用  │
│  10分钟内存   │ │ 延迟加载     │ │  滑动限流    │
│  禁言调用     │ │ 关键词匹配   │ │  超时/异常   │
└──────────────┘ └──────────────┘ └──────┬───────┘
                                        │
                                        ▼
                                 ┌──────────────┐
                                 │content_filter│
                                 │ 粗口替换     │
                                 │ 广告截断     │
                                 │ 表情/换行清洗│
                                 │ 长度限制     │
                                 └──────────────┘
```

### 模块职责

| 模块 | 职责 | 来源 |
| --- | --- | --- |
| `__init__.py` | 群聊限定 rule、matcher 注册、消息收发、插件配置注册 | 重写（适配新版框架） |
| `data_source.py` | 路由决策、LLM 调用、降级编排、打招呼/无回复语库、统一过滤出口 | 新写 |
| `persona.py` | LLM 固定人设（帕秋莉×AnuNeko）system prompt，运行时填昵称 | 新写 |
| `message_manager.py` | 复读/学舌检测，10分钟内存，禁言仅群聊 | 移植自 ai 插件 |
| `anime_store.py` | anime.json 延迟加载、容错、迁移、匹配 | 基于 ai 新版改进 |
| `qyk_client.py` | 青云客调用、限流、超时、黑名单、非 JSON 防护 | 新写 |
| `rate_limiter.py` | 滑动窗口限流器（纯逻辑，无框架依赖，可单测） | 新写 |
| `content_filter.py` | 青云客清洗管道 + 全局敏感词过滤（纯逻辑） | 基于 ai 插件逻辑扩展 |
| `config.py` | 配置读取封装（get_config 异常兜底） | 新写 |

---

## 三、路由决策伪代码

> 以下为设计阶段伪代码，仅用于表达路由逻辑与优先级；**具体函数名、参数与过滤出口以 `data_source.py` 实际源码为准**（如 `contains_question`、`clean_qyk_content`、`qyk_client.is_blocked`、`_build_text`）。

```python
async def route_message(text, image, user_id, nickname, is_group):
    # 1. 复读/学舌检测（最高优先级，防刷屏）
    special = ai_message_manager.get_result(user_id, nickname)
    if special:
        ai_message_manager.add_result(user_id, special)
        return special

    # 2. 打招呼语库
    if not text and not image:
        return hello()
    if text in GREETING_WORDS:
        return hello()

    # 3. 图片消息 → 直接走 LLM（青云客不支持图片）
    if image:
        result = await llm_chat(text, image, user_id)
        return result or no_result()

    # 4. 短消息分支
    if len(text) < SHORT_MSG_THRESHOLD:
        # 4.1 疑问词识别：短消息但在提问，直接走 LLM
        if contains_question_word(text):
            return await llm_with_fallback(text, user_id)

        # 4.2 anime 概率门控
        if random.random() < ANIME_PROBABILITY:
            anime_reply = anime_store.match(text, nickname)
            if anime_reply:
                ai_message_manager.add_result(user_id, anime_reply)
                return anime_reply

        # 4.3 青云客概率门控（带限流和黑名单）
        if random.random() < QYK_PROBABILITY:
            if not qyk_client.has_blocked_keyword(text):
                qyk_reply = await qyk_client.chat(text)
                if qyk_reply:
                    qyk_reply = content_filter.clean(qyk_reply)
                    if qyk_reply:
                        # 昵称替换
                        qyk_reply = replace_nickname(qyk_reply, nickname)
                        ai_message_manager.add_result(user_id, qyk_reply)
                        return qyk_reply

        # 4.4 全部未命中 → LLM → no_result
        return await llm_with_fallback(text, user_id) or no_result()

    # 5. 长消息分支 → LLM
    return await llm_with_fallback(text, user_id) or no_result()


async def llm_with_fallback(text, user_id, image=None):
    """LLM 调用，失败降级青云客"""
    if not HYBRID_AI_CHAT_MODEL:
        # LLM 未配置 → 降级青云客（仍受限流）
        return await qyk_client.chat_safe(text) or no_result()
    try:
        reply = await llm_service.chat(text, image, model=HYBRID_AI_CHAT_MODEL)
        if reply:
            ai_message_manager.add_result(user_id, reply)
            return reply
    except LLMException:
        # LLM 失败 → 降级青云客
        return await qyk_client.chat_safe(text) or no_result()
```

---

## 四、技术痛点清单（10 项）

### 痛点 1：青云客服务端不稳定与非 JSON 响应

**现象**：天气查询返回原始 PHP Warning（`<br /><b>Warning</b>: gzdecode()...`），前缀不是 JSON；偶发请求超时（>10 秒无响应）。

**影响**：`response.json()` 直接抛异常，整个处理器崩溃。

**解决**：
- 所有请求强制 `timeout=QYK_TIMEOUT`（默认 8 秒）。
- 先 `try: json.loads`，非 JSON 直接返回 `None` 降级，**不向上抛异常**。
- 关键词黑名单中直接屏蔽"天气"，避免触发已知故障路径。
- 对返回的 `result` 字段做存在性判断，不假设 `result == 0`。

### 痛点 2：青云客内容文明度与本地审核成本

**现象**：
- 日常聊天带粗口（"死一边去""哈个屁"）。
- 笑话功能返回超长低俗文本。
- 官方 `ai` 插件依赖 ALAPI 云端内容审核（`censor/text`），需要 ALAPI token，免费额度有限且不稳定。

**解决（本插件实现不依赖 ALAPI）**：
- 完全不引入 ALAPI（免费额度不稳定、增加网络依赖），改用纯本地清洗管道：
  - `QYK_BLOCK_KEYWORDS`：笑话、天气等关键词直接不走青云客。
  - `QYK_SWEAR_FILTER`：粗口词表本地替换为等长 `*`。
  - 广告截断：包含"公众号"、"淘宝"、"taobao"直接判空。
  - 长度限制：回复超过 200 字截断（防止超长笑话）。
- **坦诚的取舍**：本地词表无法做到 100% 准确，这是"免费 API"的固有成本。降低青云客触发概率（0.3）+ 全局限流是更有效的兜底手段。

### 痛点 3：青云客限流的精度问题

**现象**：官方限制 200 次/10 分钟，超限后返回内容包含"请求次数超过"，且限流是**全局 IP 维度**的，不是按群或按机器人实例。

**风险**：
- 多个群同时活跃会快速消耗配额。
- 重启后内存计数器清零，可能瞬时超限。
- 青云客没有 appid 区分，无法按用户隔离。

**解决**：
- 采用**滑动窗口计数**（`deque` 存时间戳），而非固定窗口，避免边界突刺。
- 默认上限设为 **100 次/10 分钟**（官方上限的 50%），预留安全余量。
- 超限后所有分支统一降级到 LLM 或 no_result，不做请求重试。
- 计数器保存在内存中（进程级），重启重置是可接受的（官方窗口也是滑动的）。

### 痛点 4：anime.json 子串匹配误伤

**现象**：`text.find(key) != -1` 是子串匹配，会产生以下误伤：
- "傻" 命中 "傻瓜相机"、"傻乐" 等正常用词。
- "抱" 命中 "抱歉"、"抱怨"。
- "亲" 命中 "亲戚"、"父亲"、"亲手"。
- "贴" 命中 "贴心"、"补贴"。

**影响**：用户在问正经问题时收到萌系互动回复，体验割裂。

**解决（分层防御）**：
1. **疑问词前置**：短消息中包含"吗、呢、怎么、什么、为什么、哪、谁、？、?"时，直接走 LLM，不进入 anime 匹配。
2. **长度限制**：只在 `< SHORT_MSG_THRESHOLD`（默认 6 字）的短消息中匹配，长消息不受影响。
3. **概率门控**：即便通过了前两层，仍只有 70% 概率进入 anime 分支。
4. **可选优化**：配置文件支持 `ANIME_STRICT_KEYWORDS`（如"傻"、"裸"等容易误伤的词），对这些词改为整词匹配或直接删除。

### 痛点 5：anime.json 加载容错

**现象**：旧版 ai 插件在模块顶层 `json.load(open(...))`，文件不存在时**整个插件加载失败**。

**解决**：
- 延迟加载：首次调用 `match()` 时才读取文件。
- 文件不存在时打印 warning，返回空字典，anime 分支静默跳过。
- 首次启动时自动把插件目录内的 `anime.json` 迁移到 `DATA_PATH/anime.json`。
- JSON 解析失败时 catch 异常，不影响其他分支。

### 痛点 6：LLM 与语库的消息记录同步

**现象**：
- AiMessageManager 通过 `add_message()` 和 `add_result()` 记录对话，用于检测复读/学舌。
- 如果某个分支（anime、青云客）直接 return 了结果但没调 `add_result()`，后续用户复读该回复时，学舌检测会漏判。
- 反之，如果 LLM 回复后重复记录，会造成数据污染。

**解决**：
- **统一出口原则**：所有可能产生最终回复的分支，在返回前必须调用 `ai_message_manager.add_result()`。
- 在路由函数内做统一后处理，而不是散落在各分支中：
  ```python
  reply = await route_message(...)
  if reply:
      ai_message_manager.add_result(user_id, reply)
  ```
- 注意 `hello()` 和 `no_result()` 是**不参与学舌检测**的（它们是随机语库，不是针对用户内容的回复），不需要 add_result。
- AiMessageManager 只负责复读检测，**不参与 LLM 上下文管理**，职责单一。

### 痛点 7：block 与事件传播

**现象**：
- 官方 ai：`on_message(rule=to_me(), priority=998)`，block 默认 True。
- bym_ai：`on_message(priority=998, block=False, rule=bym_ai_rule)`。
- 缝合版作为唯一 AI 插件，必须 block=True，否则会与其他同 priority 插件产生重复响应。

**解决**：
- 固定使用 `block=True`，确保进入此插件的 @ 消息不再向其他 AI 插件传播。
- 复读检测仅在 `to_me()` 消息上生效（这与官方 ai 一致）。
- 不做 bym_ai 那种"非 @ 消息也嗅探"的行为——那是为了群模式上下文记忆，而本插件不做群模式记忆。

### 痛点 8：昵称替换的链式问题

**现象**：
- anime 回复中 `"你" → 用户昵称`。
- 青云客回复中 `"菲菲" → 机器人昵称`。
- 官方 ai 还会做 `"小主人" → 用户昵称`、`"小朋友" → 用户昵称`。
- 昵称可能包含特殊字符（空格、正则元字符），`str.replace` 是安全的，但正则替换要转义。
- 波浪线装饰（`~昵称~`）只在 `len(nickname) < 5` 且 50% 概率时触发。

**解决**：
- 全部使用 `str.replace`，不使用正则。
- 昵称替换分两层：
  - anime 分支：替换"你"。
  - 青云客分支：替换"菲菲"为机器人昵称，再做"小主人/小朋友"为用户昵称。
- 装饰性波浪线逻辑仅在青云客分支保留（与官方 ai 一致），anime 分支不做（anime 文本本身已经很萌）。

### 痛点 9：图片消息的路由

**现象**：
- 旧 ai 插件把图片 URL 传给图灵 API。
- 青云客不支持图片。
- 缝合版没有图灵。
- 纯图片（无文字）不应走打招呼语库（用户可能是发图提问）。

**解决**：
- 含图片的消息**直接走 LLM 多模态分支**（前提是配置了支持视觉的模型）。
- LLM 未配置或调用失败 → `no_result()`，**不降级青云客**（青云客处理不了图片）。
- 纯图片 + 无文字时，不走打招呼语库（与官方 ai 旧版行为不同——旧版会把空消息当打招呼，但这里已经有图片了）。

### 痛点 10：LLM 未配置 / 不可用的降级链

**现象**：
- 用户可能只想要免费的语库+青云客，不配置 LLM。
- 此时长消息和图片消息都会落到 LLM 分支，需要明确降级路径。
- 如果降级链路设计不当，可能出现"长消息无响应"。

**解决**：
- 配置项 `HYBRID_AI_CHAT_MODEL` 为 `None` 时视为禁用 LLM。
- 长消息降级：仍走青云客（受限流），但**不做概率门控**（因为已经没有其他选择），仅受限于频率和黑名单。
- 图片消息：直接 `no_result()`（青云客不支持图片）。
- 青云客也超限/失败：统一 `no_result()`。

---

## 五、逻辑冲突分析（7 项）

### 冲突 1：复读检测 vs 打招呼语库的优先级

**冲突场景**：用户连续发了 3 次"你好"。
- 复读检测逻辑：同一消息重复 3 次，应该触发"为什么要发一样的话"。
- 打招呼语库逻辑："你好"命中问候词，应该回复打招呼语。

**决策**：**复读检测优先于打招呼**。理由：
- 复读检测的本质是反刷屏，是行为安全机制，优先级必须高于内容路由。
- 官方 ai 插件的执行顺序也是先 `get_result` 再判断打招呼（参见 data_source.py 中 `get_chat_result` 首先调用 `ai_message_manager.get_result`）。
- 实现上，`route_message()` 第 1 步就是复读检测。

### 冲突 2：anime 概率 vs 青云客概率的门控关系

**冲突场景**：短消息同时满足 anime 关键词和青云客可用条件，应该走哪个？

**决策**：**串行门控，不是互斥二选一**。

- 第一道门：以 `ANIME_PROBABILITY`（0.7）概率决定是否**尝试** anime 匹配。
- 注意：概率未命中（30%）或关键词未匹配（70% 命中了但没找到关键词），都不直接失败。
- 第二道门：anime 未产出结果后，以 `QYK_PROBABILITY`（0.3）概率决定是否尝试青云客。
- 青云客仍需通过限流和黑名单检查。
- 全部失败 → LLM。

**实际青云客触达率估算**（假设 anime 关键词覆盖率约 40%）：
- 进入 anime 匹配：70%
- anime 实际产出回复：70% × 40% = 28%
- 进入青云客门控：72% × 30% = 21.6%
- 青云客通过限流和黑名单（假设 90%）：约 19.4%
- 剩余约 52% 进入 LLM/no_result。

**这符合"青云客不要太频繁"的设计目标。**

### 冲突 3：复读检测的内存记录 vs LLM 上下文

**冲突场景**：
- AiMessageManager 记录最近 10 分钟的消息用于复读检测。
- LLM 框架（zhenxun.services.ai）也有自己的短期记忆。
- 两套系统并行运行，消息被记录两次。

**决策**：**有意为之的双系统，不合并**。
- AiMessageManager 关注的是**行为模式**（你是不是在复读、是不是在学我说话），10 分钟窗口。
- LLM 短期记忆关注的是**语义上下文**（你刚才在聊什么）。
- 两者用途不同，合并反而会增加耦合。
- 本插件的轻量化原则：不使用 LLM 持久化记忆（ORM、向量 RAG 全部不做），LLM 每次调用时由 `zhenxun.services.ai` 自行管理短期上下文，插件层不干涉。

### 冲突 4：短消息长度阈值与疑问语义

**冲突场景**：
- "人生意义"（4 字）——按长度走短消息分支，但这显然是个复杂问题。
- "猫？"（2 字）——按长度走短消息，但带问号，用户可能在问问题。
- "在吗"（2 字）——按长度走短消息，这确实是打招呼。

**决策**：**长度阈值 + 疑问词识别，双重判断**。
- 短消息首先检查疑问词表：`吗、呢、怎么、什么、为什么、哪、谁、？、?、如何、为何、嘛（语境化）`。
- 命中疑问词 → 直接走 LLM，跳过 anime 和青云客。
- 不命中疑问词 → 继续 anime/青云客概率门控。
- 疑问词表可配置（`QUESTION_WORDS`），用户可以根据实际效果调整。

### 冲突 5：禁言逻辑在私聊中的行为

**冲突场景**：复读检测在私聊中也会触发（`BanConsole.ban` 在官方 ai 中对群聊和私聊都调用），但私聊场景下"禁言"没有意义，且可能产生异常。

**决策**：
- 复读/学舌的**文本回复**在群聊和私聊都生效。
- **禁言动作仅在群聊中调用**（`is_group` 判断），私聊中跳过 `BanConsole.ban`，只输出警告文本。
- 这与官方 ai 旧版直接调用 `BanUser.ban` 的行为略有不同，但更合理（私聊禁言依赖具体 adapter 实现，可能抛异常）。

### 冲突 6：全局 TEXT_FILTER 与青云客专属过滤的顺序

**冲突场景**：
- 全局 `TEXT_FILTER` 默认含 `["鸡", "口交"]`。
- 青云客专属 `QYK_SWEAR_FILTER` 含 `["滚", "去死", ...]`。
- LLM 回复只过全局过滤，不过青云客专属过滤。
- 两个过滤词表如果重复维护，容易不一致。

**决策**：
- 两层过滤，职责不同：
  - **青云客清洗管道**（`content_filter.clean_qyk_content()`）：在 `qyk_client` 内、青云客返回结果后立即执行，只处理青云客特有问题（菲菲替换、`{br}`、`{face}`、广告、粗口、长度）。
  - **全局 TEXT_FILTER**（`data_source._build_text()` 统一出口）：复读/anime/青云客/LLM 等所有**动态文本**在构建 UniMessage 前统一过一遍；固定语库 `hello()/no_result()` 本身不含敏感词，不走此函数。
- `QYK_SWEAR_FILTER` 是青云客的专属补丁，不影响 LLM 输出。
- 全局 `TEXT_FILTER` 保持与官方 ai 一致的默认值，方便用户迁移。

### 冲突 7：消息长度统计与 CQ 码

**冲突场景**：
- 用户消息可能是 `[CQ:face,id=178]你好` 这种纯文本 + CQ 码混合。
- 长度统计必须基于**提取后的纯文本**，不能把 CQ 码算进去。
- 纯 CQ 表情消息（无文字）应该怎么处理？

**决策**：
- 所有文本通过 `message.extract_plain_text()` 提取，再统计长度。
- 含图片的消息走图片分支（不依赖文本长度）。
- 纯表情、无文本、无图片的消息（理论上极少）→ 走 `hello()` 打招呼语库（与官方 ai 空消息行为一致）。

---

## 六、概率与限流的定量设计

### 6.1 青云客触达率计算

设单群消息速率为 R 条/分钟，短消息占比为 s（假设 0.5），青云客关键词黑名单命中率为 b（假设 0.1）：

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `ANIME_PROBABILITY` | 0.7 | 进入 anime 匹配的概率 |
| anime 关键词覆盖率（177 词） | 约 0.4 | 短消息中实际命中关键词的比例 |
| anime 实际产出率 | 0.7 × 0.4 = 0.28 | |
| `QYK_PROBABILITY` | 0.3 | anime 未产出后走青云客的概率 |
| 黑名单命中率 | 0.1 | 笑话/天气等不调青云客 |
| 青云客实际触达率（短消息） | (1-0.28) × 0.3 × 0.9 ≈ **0.194** | 约 19.4% |

**单群速率估算**：假设一个活跃群每分钟 5 条 @ 消息，其中短消息 2.5 条，青云客实际调用约 0.49 次/分钟 ≈ **49 次/10 分钟**。
单群占用配额约 49%，两个群接近满载，三个群触发限流降级。

这是可接受的：触发限流后自动降级到 LLM 或 no_result，不会产生用户可感知的错误。

### 6.2 滑动窗口实现

```python
from collections import deque
import time

class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls = max_calls
        self.window = window_seconds
        self._timestamps: deque[float] = deque()

    def acquire(self) -> bool:
        now = time.time()
        # 清理过期时间戳
        while self._timestamps and now - self._timestamps[0] > self.window:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_calls:
            return False
        self._timestamps.append(now)
        return True
```

---

## 七、风险与未解决的问题

| 风险 | 严重程度 | 说明 | 缓解 |
| --- | --- | --- | --- |
| 青云客未来彻底关停 | 中 | 免费 API 无 SLA，随时可能下线 | 所有青云客失败均降级；下线后插件仍可用语库 + LLM |
| 本地粗口词表覆盖不全 | 中 | 无法穷尽所有不文明表达，且本插件不引入 ALAPI | 低概率触发 + 可配置词表（`QYK_SWEAR_FILTER`/`TEXT_FILTER`）+ 黑名单 + 截断 |
| anime 子串匹配误伤 | 低 | 单字键（如"是"）可能命中长句 | 疑问词前置（`contains_question`）+ 长消息不进 anime + 概率门控，实际路由中已规避 |
| LLM 模型名称变更 | 低 | DeepSeek 模型版本可能更新 | 模型名在配置中，可随时修改 |
| 与其他 AI 插件冲突 | 高 | 同 priority 注册 | README 明确声明二选一，不提供共存 |
| 青云客限流是 IP 维度 | 中 | 无法按群精确隔离 | 保守阈值（100/10min）+ 降级链 |
| 图片消息依赖视觉模型 | 低 | 非视觉模型无法处理图片 | 配置说明中明确标注模型需支持多模态 |

---

## 八、与 bym_ai 的功能取舍对照

| 功能 | bym_ai | hybrid_ai | 取舍原因 |
| --- | --- | --- | --- |
| LLM 对话 | ✅ | ✅ | 核心 |
| 多模态图片 | ✅ | ✅ | 核心 |
| 短期记忆（ORM） | ✅ | ❌ | 轻量化，用 zhenxun 内置短期上下文 |
| 长期向量记忆 RAG | ✅ | ❌ | 复杂、需要 embedding 模型、成本高 |
| 群组双缓冲 | ✅ | ❌ | 复杂，仅群模式需要 |
| LLM 总结压缩 | ✅ | ❌ | 依赖长期记忆 |
| 人设系统 | ✅ | ❌ | 简化为单一人设（可在 LLM 配置中设） |
| 人设护栏 | ✅ | ❌ | 每次回复额外消耗 Token |
| 发言门控 | ✅ | ❌ | 每次回复额外消耗 Token |
| 随机插话 | ✅ | ❌ | 仅在 @ 时响应，行为可预期 |
| TTS 语音 | ✅ | ❌ | 非核心 |
| 签到好感度 | ✅ | ❌ | 强耦合签到插件 |
| 群组独立配置 | ✅ | ❌ | 全局配置即可 |
| 复读/学舌检测 | ❌ | ✅ | 移植自 ai |
| 打招呼语库 | ❌ | ✅ | 移植自 ai |
| anime.json 语录 | ❌ | ✅ | 移植自 ai |
| 青云客 | ❌ | ✅ | 低频补充 |
| 无回复语库 | ❌ | ✅ | 移植自 ai |
| 失忆卡道具 | ✅ | ❌ | 无持久化记忆则不需要 |

**一句话总结**：hybrid_ai 保留"ai 插件的萌系反应层"，借用"bym_ai 的 LLM 处理能力"，砍掉所有重型记忆与控制流组件。

---

## 九、LLM 配置联动方案（真实 zhenxun 环境验证）

### 9.1 zhenxun 全局 AI 配置结构

zhenxun 的 AI 配置存储在 `Config.get("AI")`（即 `data/configs/plugins2config.yaml` 的 `AI` 组），由 `LLMConfig` 模型管理，核心字段：

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `default_models.chat` | 全局默认聊天模型 | `Gemini/gemini-3.5-flash` |
| `providers` | 提供商列表（name/api_key/api_base/api_type/models） | 含 DeepSeek/Doubao/siliconflow/GLM/Gemini 模板 |
| `model_groups` | 虚拟模型路由组 | `cheap_models` 等 |

**关键发现**：zhenxun 原生支持 **Doubao（豆包）** 提供商，`api_type=doubao`，`api_base=https://ark.cn-beijing.volces.com/api`，模型如 `doubao-seed-1-6-250615`。用户只需在后台填入自己的火山引擎 API key 即可使用。

### 9.2 联动机制

`zhenxun.services.ai.llm.chat(message, model=None, ...)` 的行为：
- `model=None` → 自动调用 `get_default_model("chat")`，使用全局默认模型
- `model="Doubao/doubao-seed-1-6-250615"` → 覆盖为指定模型

本插件的联动设计：
1. `HYBRID_AI_CHAT_MODEL` 注册默认值为 `None`（留空）
2. `llm_chat()` 中，配置值为 falsy 时**不传 `model` 参数**给 `chat()`
3. zhenxun 自动使用全局默认 chat 模型，API key/base_url 从全局 AI 配置读取
4. 用户如需插件使用不同模型，在后台填写具体模型名即可覆盖

### 9.3 为什么不直接读 zhenxun 配置文件

| 方案 | 优雅性 | 风险 |
| --- | --- | --- |
| **当前方案**：通过 `chat(model=None)` 联动 | ✅ 优雅 | 无（依赖 zhenxun 官方抽象层） |
| 直接读 `plugins2config.yaml` 的 AI 组 | ❌ 不优雅 | 绕过 ConfigsManager 抽象；文件路径/结构是内部实现，可能随版本变；失去类型转换/热更新/后台编辑；并发读写风险 |
| 插件自己硬编码 API key/base_url | ❌ 不优雅 | 安全风险；无法复用全局配置；多处维护 key |
| 自己实现 HTTP 调用绕过 `zhenxun.services.ai` | ❌ 不优雅 | 重复造轮子；失去多模态/重试/上下文管理；无法与 bym_ai 共享配置 |
| 修改 zhenxun 全局配置来设置插件模型 | ❌ 不优雅 | 插件不应修改全局配置；可能影响其他插件；升级时被覆盖 |

**核心原则**：插件依赖 zhenxun 的**抽象接口**（`zhenxun.services.ai.llm.chat`），而非**具体实现**（配置文件路径/结构）。这与 bym_ai 的做法完全一致。

### 9.4 get_config 的默认值兜底

zhenxun 的 `init_config`（内置插件）在完整启动时才会把插件的 `PluginExtraData.configs` 写入 `ConfigsManager`。若配置文件缺失或 `init_config` 未运行，`Config.get("hybrid_ai")` 返回空 `ConfigGroup`，`ConfigGroup.get(key)` 返回 `None`。

因此 `config.py` 维护了一份 `_DEFAULTS` 字典，与 `__init__.py` 中 `RegisterConfig` 的默认值保持一致，在配置未写入时兜底。

### 9.5 验证结果（真实 zhenxun 0.2.4 环境）

- ✅ `nonebot.init()` + 注册 OneBot V11 适配器后，`zhenxun.configs`、`zhenxun.services.ai.llm` 正常导入
- ✅ 插件通过 `nonebot.load_plugins()` 加载成功，`anime.json` 自动迁移到 `data/anime.json`（177 关键词）
- ✅ `Config.get("hybrid_ai")` 为空时，`get_config` 返回正确本地默认值（SHORT_MSG_THRESHOLD=6 等）
- ✅ `HYBRID_AI_CHAT_MODEL=None` 时，`llm_chat()` 调用 `chat()` **不传 model**，zhenxun 使用全局默认 `Gemini/gemini-3.5-flash`
- ✅ 人设 `instruction` 和 `timeout` 正确传入 `chat()`
- ✅ 打招呼语库、anime 语库、LLM mock 分支均正常
- ⚠️ `AsyncHttpx` 需 `PriorityLifecycle.on_startup` 初始化全局 client；最小化测试中未触发 startup 导致青云客调用报"全局 httpx.AsyncClient 未初始化"，真实启动时无此问题
