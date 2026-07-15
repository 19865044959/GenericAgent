# GA 记忆系统技术分析：从零到 Embedding 语义检索

## Context

GenericAgent (GA) 是一个多轮对话 AI Agent 框架。在优化之前，GA **没有任何自动记忆系统**——每次会话从零开始，Agent 对用户一无所知。用户需要在每次对话中重复介绍自己的偏好、技术栈、项目背景。

两次优化（commit `c082458` + `c1cc33b`）从零构建了一套完整的**自动记忆系统**，实现闭环：用户输入 → 检索相关记忆 → 注入上下文 → LLM 回答 → 会话结束时 LLM 萃取 → 持久化存储。

---

## 优化一：L2 自动记忆基础设施（commit `c082458`）

### 技术点

#### 1. 三段式记忆流水线

新增 `memory_auto.py`（330 行），实现三个核心函数：

```
用户输入 → search_memory() → 记忆文本注入 prompt → LLM 回答
                                                        ↓
会话结束 → extract_facts() → auto_update_l2() → global_mem.txt
```

| 函数 | 触发时机 | 职责 |
|------|----------|------|
| `search_memory(query, memory_dir)` | 每次用户输入 | 从 L2 检索相关记忆，格式化为 `[Auto Retrieved Memory]` 文本块注入 prompt |
| `extract_facts(history, llmclient, memory_dir)` | 会话结束 | 将对话历史 + 已有 L2 → LLM 萃取新事实，返回 JSON 数组 |
| `auto_update_l2(facts, memory_dir)` | 会话结束 | 写入 `global_mem.txt`，处理冲突/去重/更新 |

#### 2. 关键词检索 + 同义词扩展

检索采用**中文 2-gram + 手工同义词词典**方案：

- **分词**：提取中文 2-gram、单字、英文 3+ 字母单词
- **同义词字典**：维护 24 组映射。例如 `"衣服" → ["颜色", "风格", "偏好", "搭配", "穿着"]`，`"技术栈" → ["工具", "操作系统", "AI", "Docker", "K8s"]`
- **打分**：2-gram 命中权重 2，单字命中 ≥2 个权重 0.5，按分数排序取 top-5

#### 3. LLM 萃取 + 多级去重

- **萃取 prompt**：将**全部已有 L2** + 最近 60 条对话历史注入 LLM，输出 `[{"section": "...", "key": "...", "value": "..."}]`
- **`detect_conflicts()`**：正则逐条比对 key，发现相同 key 不同 value → 标记冲突
- **三级去重**：
  1. 完全相同 key:value → 跳过
  2. 不同 key 但 value 完全相同 → 跳过（防止 LLM 自创 key 名，如已有 `"喜欢的颜色: 卡其色"`，LLM 输出 `"颜色偏好: 卡其色"`）
  3. 冲突 → 原地替换旧值

#### 4. Agent 主循环埋点

在 `agentmain.py` 两处集成：

**检索埋点**（每次用户输入后，LLM 调用前）：
```python
retrieved = search_memory(raw_query, os.path.join(script_dir, 'memory'))
if retrieved:
    raw_query = retrieved + '\n\n' + raw_query  # 记忆前置到 prompt 开头
```

**萃取埋点**（会话结束时）：
```python
def _do_extraction(self):
    facts = extract_facts(self.handler.history_info, self.history, self.llmclient,
                          memory_dir=os.path.join(script_dir, 'memory'), timeout=15)
    if facts:
        auto_update_l2(facts, os.path.join(script_dir, 'memory'))
```
支持多种会话结束触发：正常结束、Ctrl+C 中断、`__EXIT__` 信号、批量模式结束。

### 解决了什么问题

| 问题 | 解决 |
|------|------|
| 跨会话记忆完全丢失 | LLM 自动萃取 + `global_mem.txt` 持久化 |
| 每次对话需重复自我介绍 | 每次用户输入自动检索并前置注入 |
| 记忆更新冲突 | 冲突检测 + key 粒度原地替换 |
| 记忆重复膨胀 | 三级去重（完全重复 / value 重复 / 冲突更新） |

### 遗留的天花板

1. **语义鸿沟**：用户说"最近 deadline 好焦虑"，L2 存有"压力大时喜欢听古典音乐放松"——关键词匹配零命中（"焦虑" ≠ "古典"）
2. **规模膨胀致噪音**：L2 到 30+ 条后，关键词"偏好"平等命中颜色偏好、饮食偏好、IDE偏好……top-5 中 3/5 可能跟当前 query 无关
3. **萃取 token 膨胀**：`extract_facts()` 将整个 `global_mem.txt` 传给 LLM。L2 达到 200 条 ≈ 3000+ tokens，不断增加

---

## 优化二：关键词 → Embedding 语义检索（commit `c1cc33b`）

### 技术点

#### 1. 可插拔 Embedding 后端（`memory/embedder.py`，300 行）

设计三级后端降级链，优先级从高到低：

```
ONNX Runtime (bge-small-zh-v1.5 / all-MiniLM-L6-v2)    ← 本地 AI 模型，~10ms
    ↓ 不可用
HTTP API (OpenAI 兼容接口)                               ← 需要网络，~200ms
    ↓ 不可用
NgramFallback (纯 numpy，零依赖)                         ← 保底方案，<1ms
```

**ONNX 后端** (`OnnxEmbedder`)：
- 自动检测 `memory/models/` 下的 ONNX 模型文件，中文 `bge-small-zh-v1.5`（94MB）优先，英文 `all-MiniLM-L6-v2`（90MB）为 fallback
- `tokenizers` 库加载 tokenizer → ONNX Runtime 推理 → mean pooling → L2 归一化
- 纯 CPU 推理，~10ms/次

**API 后端** (`APIEmbedder`)：
- 兼容 OpenAI `/v1/embeddings` 接口，通过环境变量 `EMBED_API_BASE`、`EMBED_API_KEY`、`EMBED_MODEL` 配置
- 默认 `text-embedding-3-small`（1536 维）

**NgramFallback 后端** (`NgramFallbackEmbedder`)：
- **纯 numpy，零外部依赖**，任何时候都能跑
- 中文提取 uni/bi/tri-gram，英文提取完整单词 + char 3-gram
- 用 `hash(feature)` 做确定性种子生成 384 维随机向量（同词永远同向量），TF-IDF 风格加权
- 维度与 `all-MiniLM-L6-v2` 对齐（384 维），切换后端时下游代码无需修改
- 本质是 **Random Indexing** 技术：用随机投影把高维稀疏 n-gram 空间映射到低维稠密空间，Johnson-Lindenstrauss 引理保证近似保持距离关系——共享 n-gram 越多，向量越接近

#### 2. 向量索引管理（`memory/vectors.py`，247 行）

`vectors.json` 是 `global_mem.txt` 的机器可读缓存，采用主从架构：

```
global_mem.txt  ← Source of Truth（人可读，可手动编辑）
vectors.json    ← 纯缓存（机器格式，可从 txt 随时重建）
```

核心函数：

| 函数 | 职责 |
|------|------|
| `_parse_l2_facts()` | 解析 txt 的 Markdown 结构（H2 section / H3 subsection / 事实行） |
| `fact_to_embed_text()` | 构造 embedding 文本：`"{key}: {value} [{section} > {subsection}]"` |
| `build_vectors()` | 逐条编码，生成 entries |
| `save_vectors()` | 写入 `vectors.json`（含 `l2_mtime` 时间戳元数据） |
| `load_vectors()` | 加载缓存；比较 `l2_mtime` 与 txt 当前修改时间，不一致则自动重建 |
| `search_vectors()` | 余弦相似度检索 → 过滤 `score > threshold` → 取 top-k |

**embed_text 格式设计**：`"{key}: {value} [{section} > {subsection}]"`——key:value 放在前面主导语义向量方向，层级路径放在末尾作为轻量标签。这样 `"耳机偏好: 入耳式降噪 [购物偏好 > 数码电子产品]"` 和 `"颜色偏好: 卡其色 [购物偏好 > 服装鞋帽]"` 的向量自然分开。

**`l2_mtime` 版本校验**是关键设计：`load_vectors()` 比较 txt 文件修改时间与 json 中记录的时间戳，不一致就自动重建索引——缓存永远不会过期，无需手动维护。

#### 3. `search_memory` 升级：双路径检索

流程变为：

```
search_memory(query, memory_dir)
    ├── 尝试加载 embedder + vectors
    │   ├── 成功 → search_vectors(query, entries, top_k=5, threshold=0.4)
    │   │         → 语义检索 top-5，score 低于 0.4 的自动排除
    │   └── 失败 → 降级
    └── _keyword_search(query, memory_dir)  ← 旧逻辑保留为 fallback
```

- `threshold=0.4`（经验值）：不相关的事实直接过滤，从根源上解决噪音问题
- graceful degradation：即使 ONNX 模型和 API 都不可用，NgramFallback 保证系统正常运行

#### 4. `extract_facts` 升级：增量萃取

这是优化二在**萃取端**的关键改进。旧方案将全部 `global_mem.txt` 传给 LLM；新方案只传最相关的 top-20 条：

```python
# 旧方案（优化一）
existing_l2 = open('global_mem.txt').read()      # 全部，可能 200+ 条

# 新方案（优化二）
relevant = _get_relevant_facts(conversation, memory_dir, top_k=20)
existing_l2 = '\n'.join(relevant)                 # 最多 20 条
```

`_get_relevant_facts()` 用对话文本的 embedding 检索与对话最相关的已有事实，threshold=0.0 确保取够 top_k。prompt 中新增严格规则：要求 LLM **必须复用已有 key 名**（如已有 `"颜色偏好"`，不要自创 `"喜欢的颜色"`）。

**Token 节省效果**：L2 有 200 条事实时，旧方案 ≈ 3000+ tokens，新方案 ≤ 20 条 ≈ 300 tokens，**节省约 90%**。

#### 5. `auto_update_l2` 升级：写入后同步索引

```python
if added > 0 or updated > 0:
    with open(l2_path, 'w') as f:
        f.write(existing)
    _sync_vectors(memory_dir)  # ← 新增：自动重建 vectors.json
```

`_sync_vectors()` 调用 `build_vectors() + save_vectors()` 并清除模块级缓存，保证下次检索使用最新数据。

#### 6. 模块级向量缓存

```python
_vectors_cache = None  # (vec_path, l2_mtime, entries)

def _get_vectors(memory_dir, force_reload=False):
    if not force_reload and _vectors_cache is not None:
        cached_path, cached_mtime, entries = _vectors_cache
        if cached_path == vec_path and cached_mtime == l2_mtime:
            return entries  # 命中缓存，跳过 JSON 反序列化 + numpy 转换
```

同一次会话中多次检索只加载一次向量。

### 优化点总结

| 维度 | 优化一（关键词） | 优化二（Embedding） |
|------|:-----------:|:-----------:|
| 检索原理 | 字符串匹配 + 手工同义词词典（24 组） | 余弦相似度语义排序 + 阈值过滤 |
| 噪音控制 | 无，有命中就返回 | `threshold=0.4` 过滤，不相关直接排除 |
| 语义鸿沟 | 无法跨词汇关联 | Transformer 捕捉语义相似性 |
| 萃取 token | 全部 L2 传给 LLM | 增量模式：仅传 top-20 相关事实 |
| 环境依赖 | 无 | 三级降级：ONNX → API → NgramFallback（零依赖保底） |
| 索引维护 | 无（直接读 txt） | `l2_mtime` 自动版本校验 + 写入时自动重建 |

### 解决了什么问题

| 问题（优化一遗留） | 优化二的解决 |
|------|-------------|
| **语义鸿沟**："焦虑" 与 "压力大时放松" 匹配不上 | Embedding 语义相似度可跨词汇关联（如 "焦虑" embedding 与 "压力" 相近） |
| **噪音爆炸**：关键词 "偏好" 平等命中所有偏好类事实 | Cosine similarity 阈值过滤：无关事实 score 低于 0.4 直接排除 |
| **Token 爆炸**：萃取时 200 条全传给 LLM | 增量萃取：top-20 相关事实，节省约 90% token |
| **LLM 自创 key 名**：已有 "颜色偏好"，LLM 输出 "喜欢的颜色" | Prompt 强化复用规则 + value 级去重（不同 key 同 value 跳过） |
| **冷启动/依赖缺失**：必须手动安装模型库 | 三级降级链，NgramFallback 纯 numpy 零依赖保底 |

---

## 架构全景

```
┌──────────────────────────────────────────────────────────────┐
│                      agentmain.py                            │
│                                                              │
│   每次用户输入                    会话结束                     │
│   ┌──────────────┐              ┌──────────────┐             │
│   │ search_memory │              │ extract_facts│             │
│   │  检索 L2 记忆  │              │  LLM 萃取    │             │
│   └──────┬───────┘              └──────┬───────┘             │
│          │                             │                     │
│          ▼                             ▼                     │
│   注入 prompt 开头                auto_update_l2             │
│                                  写入 txt + 同步索引          │
└──────────────────────────────────────────────────────────────┘
           │                             │
           ▼                             ▼
┌──────────────────────┐    ┌──────────────────────┐
│   memory_auto.py     │    │   memory/vectors.py   │
│                      │    │                      │
│  search_memory ──────┼───▶│  load_vectors()      │
│  (embedding 优先,    │    │  search_vectors()    │
│   关键词 fallback)    │    │  build_vectors()     │
│                      │    │  save_vectors()      │
│  extract_facts ──────┼───▶│                      │
│  (增量模式: top-20)   │    └──────────┬───────────┘
│                      │               │
│  auto_update_l2 ─────┼──▶ _sync_vectors()
│  (写入后重建索引)     │
└──────────────────────┘
           │                      │
           ▼                      ▼
┌──────────────────┐    ┌──────────────────┐
│  global_mem.txt  │    │   vectors.json   │
│  (Source of      │◀───│   (机器缓存,      │
│   Truth, 人可读)  │    │   可随时重建)     │
│                  │    │                  │
│  Markdown 结构:   │    │   含 l2_mtime    │
│  ## [Section]    │    │   自动版本校验    │
│  ### Subsection  │    │                  │
│  - key: value    │    │                  │
└──────────────────┘    └──────────────────┘
                                 │
                                 │ 编码
                                 ▼
                        ┌──────────────────┐
                        │ memory/embedder.py│
                        │                  │
                        │ ONNX (优先)       │
                        │  bge-small-zh    │
                        │  all-MiniLM-L6   │
                        │  ↓ 不可用        │
                        │ API (备选)        │
                        │  OpenAI 兼容     │
                        │  ↓ 不可用        │
                        │ NgramFallback    │
                        │  纯numpy零依赖   │
                        └──────────────────┘
```

---

## Benchmark 测试用例

### 用例 1：噪音对比

31 条事实填充 L2（模拟重度用户），对比关键词 vs Embedding：

| Query | 关键词噪音率 | Embedding 噪音率 |
|-------|:--------:|:----------:|
| "帮我推荐几件衣服" | ~60% (饮食/饮品/音乐等也被召回) | ~0% |
| "最近 deadline 快到了，好焦虑" | 无匹配或全噪音 | 命中音乐偏好 |
| "帮我写个 Python 脚本" | ~40% | ~0% |
| "最近想换个编辑器" | ~40% | ~0% |

### 用例 2：语义鸿沟

```
L2 含: "音乐偏好: 古典音乐"（上下文：压力大时听古典放松）
Query: "最近 deadline 快到了，好焦虑"

关键词: 无匹配（"焦虑" ≠ "压力" ≠ "音乐"）
Embedding: ✅ 命中 "音乐偏好: 古典音乐"（语义相近）
```

### 用例 3：萃取 Token 节省

```
L2: 200 条事实
对话: "我最近想学 Go 语言"

旧方案: 200 条全传 LLM ≈ 3000+ tokens
新方案: top-20 相关 ≈ 300 tokens，节省 ~90%
```

---

## 文件变更清单

### 优化一（c082458）

| 文件 | 操作 | 说明 |
|------|------|------|
| `memory_auto.py` | 新增 | 核心记忆模块（检索 + 萃取 + 写入） |
| `agentmain.py` | 修改 | 检索埋点 + 萃取埋点 |
| `benchmark/run_benchmark.py` | 新增 | E2E 基准测试 |
| `benchmark/test_cases.json` | 新增 | 测试用例定义 |

### 优化二（c1cc33b）

| 文件 | 操作 | 说明 |
|------|------|------|
| `memory/embedder.py` | 新增 | 三级可插拔 embedding 后端 |
| `memory/vectors.py` | 新增 | 向量索引管理 |
| `memory/vectors.json` | 新增 | 向量缓存文件 |
| `memory/models/` | 新增 | ONNX 模型（bge-small-zh-v1.5 + all-MiniLM-L6-v2） |
| `memory_auto.py` | 修改 | 升级双路径检索 + 增量萃取 + 索引同步 |
| `benchmark/test_noise.py` | 新增 | 关键词噪音验证 |
| `benchmark/test_embedding.py` | 新增 | 关键词 vs Embedding 对比测试 |

---

## 核心设计原则

1. **主从架构**：人可读的 `global_mem.txt` 是 source of truth，`vectors.json` 是纯缓存——删除 json 不影响数据完整性，下次检索自动重建
2. **渐进增强**：embedding → 关键词 两级 fallback；ONNX → API → NgramFallback 三级降级——任何环境下系统都能正常运行
3. **确定性**：NgramFallback 用 `hash(feature)` 而非 `random()`，保证同词同向量，重启不改变检索结果
4. **维度对齐**：NgramFallback 的 384 维与 ONNX 模型一致，切换后端时下游代码无需改动
5. **l2_mtime 自动校验**：通过文件修改时间检测缓存失效，无需手动维护版本号或清理缓存

---

## 不做的（后续方向）

- 不做向量数据库（FAISS/Milvus/ChromaDB）——当前规模（< 200 条）numpy 暴力计算足够
- 不做动态阈值调参——0.4 是经验值，后续可通过 benchmark 数据自动校准
- 不做 L3/L4 的自动检索——当前只覆盖 L2（用户画像层）
- 不做检索门控——每次用户输入都检索，不做"是否需要检索"的判断
