"""内置 AI 提示词模板。

所有提示词都集中在这里，方便修改。用户也可以在“设置页 -> 提示词”里覆盖，
覆盖内容保存在 app_settings.prompt_overrides 中，键名与下面的常量键一致。

支持两种占位符：
- {name}        : 使用 str.format 填充
- {{name}}      : 同上（当模板里出现字面量大括号时用它）
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 一、续写正文（本模板由需求方指定，原样内置，禁止改动结构）
# ---------------------------------------------------------------------------
CONTINUE_CHAPTER = """# Role
你是一位专业的网文爽文续写作者，擅长模仿原有文风，保持剧情连贯，节奏快，爽点足。

# Context
- 已有大纲：{outline}
- 核心设定：{core_settings}
- 未回收伏笔：{foreshadows}
- **当前剧情状态（最重要，必须严格对齐）**：{story_state}
- 上一章内容：{previous_tail}

# Task
根据上述上下文，续写第 {chapter_number} 章。
- 本章目标：{goal}
- 字数要求：{target_words}字
- 风格要求：严格模仿原作者的文风，包括常用词汇、句式、节奏、对话习惯。
- 爽文要求：节奏快，冲突直接，爽点密集，少废话，少抒情，多行动和对话。
- 禁止：不要解释，不要总结，不要出现"作为AI"之类的话。

{chapter_plan}

# 硬性一致性要求（违反即为失败）
1. **必须以上一章的结尾作为本章开头**，接着写，不要跳过、不要回顾、不要另起炉灶。
2. 人物的**位置、正在做的事、持有物品、已知信息**必须与上面的「当前剧情状态」完全一致。
3. 人物若要在本章移动或易手物品，必须在正文里写清楚过程，不能凭空改变。
4. 人物不得知道他在状态里「未标明已知」的信息（避免未卜先知）。
5. 本章结束时，人物的位置与状态要交代清楚，方便下一章衔接。

# Output Format
直接输出小说正文，无需任何解释。"""


# ---------------------------------------------------------------------------
# 二、只生成大纲
# ---------------------------------------------------------------------------
OUTLINE_ONLY = """# Role
你是一位资深网文编辑，擅长设计爽文的章节节奏与爽点分布。

# Context
- 全书总纲：{outline}
- 核心设定：{core_settings}
- 未回收伏笔：{foreshadows}
- 当前剧情状态：{story_state}
- 上一章内容：{previous_tail}

# Task
为第 {chapter_number} 章设计详细大纲。
- 本章目标：{goal}
- 计划字数：{target_words}字
- 按"场景"拆分，每个场景给出：地点、出场人物、冲突点、爽点、结尾钩子。
- 明确列出本章需要推进的伏笔与需要新埋的伏笔。
- 只输出大纲，不要写正文。

# Output Format
使用 Markdown 小标题 + 无序列表。"""


# ---------------------------------------------------------------------------
# 三、润色正文
# ---------------------------------------------------------------------------
POLISH_TEXT = """# Role
你是一位网文润色编辑，熟悉中文爽文的语言节奏。

# Task
在**完全不改变剧情走向、人物设定和段落顺序**的前提下，润色下面的章节正文。
- 目标章节：第 {chapter_number} 章
- 风格要求：严格模仿参考文风（见核心设定里的文风样本）。
- 重点优化：用词更精准、对话更口语化、动作描写更有画面感、删除冗余抒情。
- 保持原有字数规模（允许 ±10%），不要缩写剧情。
- 禁止解释、禁止总结、禁止输出"以下是润色结果"之类的话。

# 参考设定
{core_settings}

# 待润色正文
{source_text}

# Output Format
直接输出润色后的完整正文。"""


# ---------------------------------------------------------------------------
# 四、重写正文（保留剧情要点，重写表达）
# ---------------------------------------------------------------------------
REWRITE_TEXT = """# Role
你是一位网文爽文写手，擅长把平淡的初稿改写成节奏快、爽点足的正文。

# Context
- 核心设定：{core_settings}
- 当前剧情状态：{story_state}
- 上一章内容：{previous_tail}

# Task
下面是第 {chapter_number} 章的初稿，剧情走向和关键事件必须保留，但整体重写一遍。
- 本章目标：{goal}
- 字数要求：{target_words}字
- 改写重点：加快节奏、强化冲突与爽点、对话更利落、删除重复与拖沓描写。
- 风格要求：严格模仿原作者的文风（见核心设定里的文风样本）。
- 禁止解释、禁止总结、禁止输出"以下是重写结果"。

# 初稿
{source_text}

# Output Format
直接输出重写后的完整正文。"""


# ---------------------------------------------------------------------------
# 五、章节摘要（拆书第一阶段：Map）
# ---------------------------------------------------------------------------
CHAPTER_SUMMARY = """你是小说拆解助手。请为下面这一批章节分别写摘要，用于后续的全局拆书。

要求：
1. **每章摘要 60~100 字**，客观陈述，不要评价。务必简短，这是硬性要求。
2. 必须包含：关键事件、出场人物、人物状态变化、新出现的地点/道具/规则。
3. `events` 最多 3 条，每条不超过 15 字；`characters` 最多 4 个名字。
4. 如果本章埋下明显线索但未解释，在 summary 末尾用"【伏笔】"标注。
5. 严格输出合法 JSON，不要任何额外文字、不要 Markdown 代码块。

JSON 格式：
{{
  "summaries": [
    {{"chapter_number": 1, "title": "章节标题", "summary": "……", "events": ["事件1"], "characters": ["人物A"]}}
  ]
}}

待处理章节：
{chapters_text}"""


# ---------------------------------------------------------------------------
# 五、全局拆书（拆书第二阶段：Reduce）
# ---------------------------------------------------------------------------
BOOK_ANALYSIS = """你是资深网文编辑与设定管理专家。下面是《{book_title}》全书的分章摘要、大纲线索和部分原文片段。
请据此输出结构化的拆书结果，用于后续 AI 续写。

已知分章摘要：
{chapter_summaries}

原文片段（用于提炼文风与用语习惯）：
{style_samples}

严格输出 JSON，不要任何额外文字，格式如下：
{{
  "synopsis": "全书总纲，300~600字，按阶段梳理主线走向",
  "outline": {{
    "volumes": [
      {{"name": "第一卷 xxx", "range": "第1-50章", "summary": "本卷主线", "key_points": ["关键剧情"]}}
    ],
    "current_stage": "当前剧情所处阶段描述",
    "next_directions": ["后续可能的剧情走向"]
  }},
  "characters": [
    {{"name": "姓名", "identity": "身份", "personality": "性格", "relations": "与主角/其他人的关系",
      "status": "当前状态（修为/职位/生死/目标）", "first_chapter": 1, "importance": "主角|重要配角|配角"}}
  ],
  "worldview": [
    {{"category": "地点|势力|规则|道具|其他", "name": "名称", "description": "说明", "first_chapter": 1}}
  ],
  "timeline": [
    {{"chapter": 1, "event": "关键事件", "impact": "对主线的影响"}}
  ],
  "foreshadows": [
    {{"content": "伏笔内容", "planted_chapter": 1, "status": "未回收",
      "possible_payoff": "可能的回收方式", "keywords": ["关键词"]}}
  ],
  "style": {{
    "tone": "整体基调与节奏",
    "common_words": ["高频词/口头禅"],
    "sentence_patterns": ["典型句式"],
    "dialogue_style": "对话习惯",
    "pacing": "节奏特征",
    "taboos": ["续写时应避免的写法"],
    "sample": "最能代表文风的一小段原文（150字以内）"
  }}
}}

硬性要求：
- characters 至少 8 个（不足则按实际数量），只保留对剧情有影响的人物。
- foreshadows 只记录"已埋下但尚未明确回收"的线索；已回收的不要列入。
- style.sample 必须逐字摘自给定原文，不要改写。
- 所有字段都要有值，没有信息时用空字符串或空数组，不要省略字段。"""


# ---------------------------------------------------------------------------
# 六、伏笔检查 / 逻辑矛盾修复
# ---------------------------------------------------------------------------
CONSISTENCY_CHECK = """你是网文剧情校对。下面给出已有设定、未回收伏笔，以及一段新写的正文。
请找出其中的设定冲突、人物性格跑偏、时间线矛盾和未回收伏笔的遗漏。

# 已有设定
{core_settings}

# 未回收伏笔
{foreshadows}

# 待检查正文
{source_text}

严格输出 JSON：
{{
  "issues": [
    {{"type": "设定冲突|人物OOC|时间线矛盾|伏笔遗漏", "detail": "问题描述", "suggestion": "修改建议", "severity": "高|中|低"}}
  ],
  "overall": "总体评价，100字以内"
}}"""


# ---------------------------------------------------------------------------
# 八、自动推演下一章目标（“让 AI 自己决定剧情”用）
# ---------------------------------------------------------------------------
PLAN_NEXT_CHAPTER = """# Role
你是资深网文主编，负责为一部正在连载的爽文规划下一章。作者把续写交给你了，请自己决定这一章写什么。

# Context
- 全书总纲与当前阶段：{outline}
- **当前剧情状态（最重要，必须严格对齐）**：{story_state}
- 尚未回收的伏笔：{foreshadows}
- 最近几章的剧情摘要：
{recent_summaries}
- 上一章内容：{previous_tail}
{chapter_plan}

# Task
为第 {chapter_number} 章做规划。要求：
1. 必须承接上一章结尾留下的钩子，不要另起炉灶。
2. **人物位置与状态必须与「当前剧情状态」一致**：谁不在场就不能突然出现，谁手上没有的东西就不能随手掏出来。
3. 优先推进或回收上面列出的伏笔，至少推进一个。
4. 节奏快、冲突直接、爽点密集；主角要有实质性收获或反击，不要原地打转。
5. 不要重复已经写过的情节，不要写回顾、不要写总结。
6. 标题是章节名，8~16 字，不要带"第X章"前缀。
7. 如果上面给了「本章既定计划」，必须按它来写，只在细节上补全，不要改写主线。

严格输出 JSON，不要任何额外文字、不要 Markdown 代码块：
{{
  "title": "本章标题",
  "goal": "本章目标，60~120字。写清楚：在哪里、谁和谁、发生什么冲突、主角怎么做、结果如何、结尾留什么钩子。",
  "foreshadows_to_advance": ["本章要推进的伏笔"],
  "hook": "结尾钩子，20字以内"
}}"""


# ---------------------------------------------------------------------------
# 七、剧情状态抽取（续写一致性的核心）
# ---------------------------------------------------------------------------
EXTRACT_STATE = """你是小说场记。下面给你「截至上一章的状态快照」「尚未回收的伏笔」和「本章正文」。
请如实记录本章结束时的客观状态，供下一章续写时对齐，避免人物位置、手上物品、已知信息前后矛盾。

# 截至上一章的状态快照
{current_state}

# 尚未回收的伏笔
{foreshadows}

# 本章正文（第 {chapter_number} 章）
{chapter_text}

# 要求
1. **只记录正文里真实写到的内容**，不要推测、不要脑补、不要引入正文之外的信息。
2. 状态是「本章结束时」的状态，不是本章开始时的状态。
3. `present_characters` 只列本章**实际出场**的人物；没出场的人物不要列。
4. `doing` 写这个人此刻正在做什么（一句话），`location` 写这个人此刻在哪；正文没交代就留空字符串。
5. `items` 记录重要物品**当前在谁手上**（易手必须记录）。
6. `known_facts` 只记「本章新产生的、后续剧情可能要用到的知情信息」。
7. `resolved_foreshadows` 填本章明确回收的伏笔（照抄待回收列表里的原文），没有就给空数组。
8. `new_foreshadows` 填本章新埋下的、尚未解释的线索，没有就给空数组。
9. `summary` 60~100 字，客观陈述本章关键事件与人物状态变化。
10. 严格输出合法 JSON，不要任何额外文字、不要 Markdown 代码块。

JSON 格式：
{{
  "summary": "本章摘要",
  "location": "本章主要场景地点",
  "time_note": "时间推进说明，例如「距上一章三天后」；无法判断则留空",
  "present_characters": [
    {{"name": "人物名", "location": "此刻在哪", "doing": "此刻在做什么",
      "mood": "情绪", "goal": "此刻的目标", "status": "身份/修为/生死等变化，无变化则留空",
      "items": ["此刻持有的重要物品"], "knows": ["此人本章新知道的关键信息"]}}
  ],
  "items": [{{"name": "物品名", "holder": "现在在谁手上", "note": "备注"}}],
  "known_facts": ["本章新产生的关键知情信息"],
  "open_threads": ["章末悬而未决的事，例如某人正在赶来"],
  "new_foreshadows": [{{"content": "新埋的线索", "keywords": ["关键词"]}}],
  "resolved_foreshadows": [{{"content": "本章回收的伏笔原文", "how": "怎么回收的"}}]
}}"""


EXTRACT_STATE_BATCH = """你是小说场记。下面给出**连续的第 {start_chapter}~{end_chapter} 章**正文。
请为**每一章**分别记录该章结束时的客观状态，供后续续写对齐，避免人物位置、物品归属、已知信息前后矛盾。

# 截至本批之前的剧情状态
{current_state}

# 尚未回收的伏笔
{foreshadows}

# 待处理章节
{chapters_text}

# 要求
1. **只记录正文里真实写到的内容**，不要推测、不要脑补。
2. 每一章单独一条，按章节号升序排列，不要漏章、不要合并章节。
3. `present_characters` 只列该章**实际出场**的人物；没出场的不列。
4. 状态是「该章结束时」的状态。正文没交代的字段留空字符串或空数组。
5. `resolved_foreshadows` 照抄待回收列表里的原文；没有就给空数组。
6. `summary` 每章 60~100 字，客观陈述该章关键事件与人物状态变化。
7. 严格输出合法 JSON，不要任何额外文字、不要 Markdown 代码块。

JSON 格式：
{{
  "states": [
    {{"chapter_number": {start_chapter},
      "summary": "本章摘要",
      "location": "本章主要场景地点",
      "time_note": "时间推进，无法判断则留空",
      "present_characters": [
        {{"name": "人物名", "location": "此刻在哪", "doing": "此刻在做什么",
          "mood": "情绪", "goal": "此刻的目标", "status": "身份/修为/生死等变化",
          "items": ["此刻持有的重要物品"], "knows": ["此人本章新知道的关键信息"]}}
      ],
      "items": [{{"name": "物品名", "holder": "现在在谁手上", "note": "备注"}}],
      "known_facts": ["本章新产生的关键知情信息"],
      "open_threads": ["章末悬而未决的事"],
      "new_foreshadows": [{{"content": "新埋的线索", "keywords": ["关键词"]}}],
      "resolved_foreshadows": [{{"content": "本章回收的伏笔原文", "how": "怎么回收的"}}]
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# 九、从零原创：故事圣经（总纲 + 人物 + 世界观）
# ---------------------------------------------------------------------------
ORIGINAL_BIBLE = """你是资深网文爽文策划。用户要你从零开始构思一部新书，请产出可直接开写的故事圣经。

# 用户设定
- 书名：{book_title}
- 题材/类型：{genre}
- 主角设定：{protagonist}
- 核心金手指/设定：{hook}
- 风格要求：{style_hint}
- 目标规模：约 {total_chapters} 章，单章约 {target_words} 字
- 补充要求：{extra}

# 要求
1. 主线必须能支撑目标章节数，分卷节奏要清晰（开局钩子 → 层层升级 → 高潮）。
2. 主角要有明确的「想要什么」和「拦路的是什么」，前 3 章必须出现第一个强冲突。
3. `characters` 至少 6 个，主角 1 个、重要配角 3 个以上，标注 `importance`。
4. `foreshadows` 给出 4~8 条可在后续回收的长线伏笔。
5. `style.sample` 留空字符串（原创没有原文可摘）。
6. 严格输出合法 JSON，不要任何额外文字、不要 Markdown 代码块。

JSON 格式：
{{
  "synopsis": "全书总纲，400~800字，按阶段梳理主线",
  "outline": {{
    "volumes": [{{"name": "第一卷 xxx", "range": "第1-30章", "summary": "本卷主线", "key_points": ["关键剧情"]}}],
    "current_stage": "开局阶段描述",
    "next_directions": ["前期主要走向"]
  }},
  "characters": [
    {{"name": "姓名", "identity": "身份", "personality": "性格", "relations": "与主角关系",
      "status": "开局状态", "importance": "主角|重要配角|配角"}}
  ],
  "worldview": [{{"category": "地点|势力|规则|道具|其他", "name": "名称", "description": "说明"}}],
  "timeline": [{{"chapter": 1, "event": "开局事件", "impact": "对主线的影响"}}],
  "foreshadows": [{{"content": "伏笔内容", "planted_chapter": 1, "status": "未回收",
    "possible_payoff": "可能的回收方式", "keywords": ["关键词"]}}],
  "style": {{"tone": "基调", "common_words": ["计划使用的标志性用词"],
    "sentence_patterns": ["计划使用的句式"], "dialogue_style": "对话习惯",
    "pacing": "节奏", "taboos": ["要避免的写法"], "sample": ""}}
}}"""


# ---------------------------------------------------------------------------
# 十、从零原创：章节计划（总纲 -> 逐章目标）
# ---------------------------------------------------------------------------
PLAN_CHAPTERS = """你是资深网文作者。根据下面的故事圣经，为这本书排出**逐章计划**。

# 故事圣经
{outline}

# 人物
{characters}

# 长线伏笔
{foreshadows}

# 已排好的上一批（保持衔接，不要重复已写过的情节）
{previous}

# Task
排出第 {start_chapter} 章到第 {end_chapter} 章的计划（共 {count} 章）。要求：
1. 每章标题 8~16 字，不要带「第X章」前缀。
2. 每章 `goal` 写清楚：在哪里、谁和谁、发生什么冲突、主角怎么做、结果如何、结尾留什么钩子，60~120 字。
3. 必须承接上一批的结尾钩子，不要另起炉灶。
4. 节奏：每 3~5 章要有一个小高潮；不要连续多章原地打转。
5. 标注该章推进了哪条伏笔（`foreshadows_to_advance`，可为空数组）。
6. 严格输出合法 JSON，不要任何额外文字、不要 Markdown 代码块。

JSON 格式：
{{
  "chapters": [
    {{"number": {start_chapter}, "title": "章节标题", "goal": "本章目标",
      "arc": "所属阶段", "foreshadows_to_advance": ["要推进的伏笔"]}}
  ]
}}"""


# ---------------------------------------------------------------------------
# 模板注册表与渲染工具
# ---------------------------------------------------------------------------
BUILTIN_PROMPTS: dict[str, dict[str, str]] = {
    "continue_chapter": {
        "name": "续写正文",
        "description": "核心续写模板，动态填充大纲/设定/伏笔/上一章结尾",
        "template": CONTINUE_CHAPTER,
    },
    "outline_only": {
        "name": "只生成大纲",
        "description": "先出章节大纲，不写正文",
        "template": OUTLINE_ONLY,
    },
    "polish_text": {
        "name": "润色正文",
        "description": "在不改剧情的前提下润色文风",
        "template": POLISH_TEXT,
    },
    "rewrite_text": {
        "name": "重写正文",
        "description": "保留剧情要点，重写表达与节奏",
        "template": REWRITE_TEXT,
    },
    "chapter_summary": {
        "name": "章节摘要（拆书 Map）",
        "description": "批量生成分章摘要",
        "template": CHAPTER_SUMMARY,
    },
    "book_analysis": {
        "name": "全局拆书（Reduce）",
        "description": "输出人物卡/世界观/时间线/伏笔/文风",
        "template": BOOK_ANALYSIS,
    },
    "consistency_check": {
        "name": "伏笔与矛盾检查",
        "description": "检查正文是否与设定冲突",
        "template": CONSISTENCY_CHECK,
    },
    "plan_next_chapter": {
        "name": "自动推演下一章（全自动续写用）",
        "description": "让 AI 自己决定下一章写什么、叫什么标题",
        "template": PLAN_NEXT_CHAPTER,
    },
    "extract_state": {
        "name": "剧情状态抽取",
        "description": "每写完一章自动抽取人物位置/行为/持有物/已知信息，写入状态层（续写一致性的核心）",
        "template": EXTRACT_STATE,
    },
    "extract_state_batch": {
        "name": "剧情状态抽取（批量）",
        "description": "一次处理连续多章，用于给导入的原文批量补建状态层，省 token",
        "template": EXTRACT_STATE_BATCH,
    },
    "original_bible": {
        "name": "原创故事圣经",
        "description": "从零原创第一步：根据题材设定产出总纲/人物/世界观/伏笔",
        "template": ORIGINAL_BIBLE,
    },
    "plan_chapters": {
        "name": "原创章节计划",
        "description": "从零原创第二步：把故事圣经排成逐章计划",
        "template": PLAN_CHAPTERS,
    },
}


def get_template(key: str, overrides: dict[str, str] | None = None) -> str:
    """取模板：优先使用用户覆盖，其次使用内置。"""
    if overrides and overrides.get(key):
        return overrides[key]
    if key not in BUILTIN_PROMPTS:
        raise KeyError(f"未知提示词模板: {key}")
    return BUILTIN_PROMPTS[key]["template"]


def render(template: str, **kwargs: object) -> str:
    """安全渲染模板：缺失的占位符保留原样，避免整条请求失败。"""

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return template.format_map(_SafeDict(**kwargs))
    except (ValueError, IndexError):
        # 模板里出现非法大括号时，退化为逐个替换
        result = template
        for key, value in kwargs.items():
            result = result.replace("{" + key + "}", str(value))
        return result


def build_prompt(key: str, overrides: dict[str, str] | None = None, **kwargs: object) -> str:
    return render(get_template(key, overrides), **kwargs)
