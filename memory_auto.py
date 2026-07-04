"""
memory_auto.py - GA 自动记忆模块
- search_memory:     语义检索 L2 记忆（embedding 优先，关键词 fallback）
- extract_facts:     LLM 萃取对话事实（增量模式：只传相关记忆去重）
- auto_update_l2:    将事实写入 global_mem.txt + 同步 vectors.json
- detect_conflicts:  检测新旧事实冲突
"""

import os, re, json


# ── Embedding 后端（延迟加载）──
_embedder = None
_vectors_cache = None  # (vec_path, l2_mtime, entries)


def _get_embedder():
    """延迟加载 embedder 单例"""
    global _embedder
    if _embedder is None:
        try:
            from memory.embedder import get_embedder
            _embedder = get_embedder()
        except ImportError:
            _embedder = None
    return _embedder


def _get_vectors(memory_dir, force_reload=False):
    """加载向量索引（带缓存）。返回 entries 列表或 []。"""
    global _vectors_cache
    l2_path = os.path.join(memory_dir, 'global_mem.txt')
    vec_path = os.path.join(memory_dir, 'vectors.json')
    embedder = _get_embedder()

    if embedder is None:
        return []

    l2_mtime = os.path.getmtime(l2_path) if os.path.exists(l2_path) else 0

    # 检查缓存
    if not force_reload and _vectors_cache is not None:
        cached_path, cached_mtime, entries = _vectors_cache
        if cached_path == vec_path and cached_mtime == l2_mtime:
            return entries

    try:
        from memory.vectors import load_vectors
        entries = load_vectors(vec_path, embedder, l2_path)
        _vectors_cache = (vec_path, l2_mtime, entries)
        return entries
    except ImportError:
        return []


def _keyword_search(query, memory_dir):
    """关键词匹配检索（旧逻辑，作为 fallback）"""
    l2_path = os.path.join(memory_dir, 'global_mem.txt')
    if not os.path.exists(l2_path):
        return ''

    with open(l2_path, 'r', encoding='utf-8') as f:
        l2_content = f.read()

    if not l2_content.strip().startswith('#') or len(l2_content.strip().split('\n')) < 3:
        return ''

    # 提取所有事实行及其所属 section
    facts = []
    current_section = ''
    for line in l2_content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('## ['):
            current_section = stripped.strip('# []').strip()
        elif stripped.startswith('- ') and len(stripped) > 2:
            text = stripped[2:].strip()
            if ':' in text or '：' in text:
                facts.append({'section': current_section, 'text': text})

    if not facts:
        return ''

    # 提取关键词：中文 2-gram + 单字 + 英文单词
    keywords_ngram = set()
    keywords_char = set()
    chinese = ''.join(c for c in query if '一' <= c <= '鿿')
    for i in range(len(chinese) - 1):
        keywords_ngram.add(chinese[i:i + 2])
    keywords_char.update(c for c in chinese)
    for w in re.findall(r'[a-zA-Z]{3,}', query.lower()):
        keywords_ngram.add(w)

    # 中文同义词扩展
    SYNONYMS = {
        '工作': ['公司', '组织', 'employer', '单位', '职业', '职位', '领域'],
        '公司': ['工作', '组织', 'employer', '单位', '职位'],
        '名字': ['姓名', 'name', '称呼', '叫'],
        '姓名': ['名字', 'name', '称呼'],
        '住': ['家', 'home', '地址', 'location', '所在地', '城市'],
        '家': ['住', 'home', '地址', 'location', '所在地'],
        '喜欢': ['偏好', 'pref', '爱好'],
        '偏好': ['喜欢', 'pref', '爱好'],
        '颜色': ['色', 'color', '色彩', '墨绿'],
        '食物': ['吃', 'food', '饮食', '菜', '火锅', '川菜'],
        '吃': ['食物', 'food', '饮食', '菜', '火锅', '川菜'],
        '饮食': ['吃', '食物', 'food', '菜', '偏好'],
        '路径': ['path', '目录', '位置', 'server', 'project', '项目'],
        '项目': ['路径', 'path', '目录', '位置', 'server', 'project'],
        '开发': ['工具', '技术', '语言', '编辑器', '环境', '配置'],
        '环境': ['系统', '配置', '工具', '开发', 'OS', '操作系统', '界面'],
        '技能': ['技术', '语言', '工具', '能力'],
        '咖啡': ['饮品', '饮料', '喝', '手冲', '美式'],
        '自我介绍': ['姓名', '工作', '城市', '所在地', '职位', '公司', '前端', '美团', '上海'],
        '总结': ['姓名', '工作', '技能', '技术', '公司', '所在地', '偏好'],
        '技术栈': ['工具', '操作系统', '技术', 'AI', 'AI工具', '语言', 'Docker', 'Cursor', 'DeepSeek', 'macOS', 'K8s'],
        '栈': ['工具', '技术', '操作系统', '语言', 'Docker', 'K8s'],
        '在哪': ['所在地', '城市', '地址', 'location', '北京', '上海', '深圳', '杭州', '广州'],
        '现在': ['所在地', '更新', '最新', '当前', '城市'],
        '什么': ['工具', '技术', '偏好', '所在地', '姓名', '公司'],
        '知道': ['姓名', '信息', '偏好', '技术'],
        '衣服': ['颜色', '风格', '偏好', '搭配', '穿着'],
        '推荐': ['偏好', '喜欢', '颜色', '风格'],
        '背景': ['技术', '职位', '公司', '所在地', '组织'],
        '前景': ['技术', '职位', '方向', '领域'],
    }
    for kw in list(keywords_ngram):
        if kw in SYNONYMS:
            keywords_ngram.update(SYNONYMS[kw])
    for kw in list(keywords_char):
        if kw in SYNONYMS:
            keywords_ngram.update(SYNONYMS[kw])
    for syn_key, syn_words in SYNONYMS.items():
        if syn_key in query:
            keywords_ngram.update(syn_words)

    if not keywords_ngram and not keywords_char:
        return ''

    # 对每个 fact 打分
    scored = []
    for fact in facts:
        search_text = (fact['section'] + ' ' + fact['text']).lower()
        score = sum(2 for kw in keywords_ngram if kw.lower() in search_text)
        if score == 0:
            char_hits = sum(1 for c in keywords_char if c in search_text)
            score = 0.5 if char_hits >= 2 else 0
        if score > 0:
            scored.append((score, fact))
    scored.sort(key=lambda x: -x[0])

    matched = [f for s, f in scored[:5] if s > 0]
    if not matched:
        return ''

    lines = ['[Auto Retrieved Memory]']
    for fact in matched:
        prefix = f"[{fact['section']}] " if fact['section'] else ''
        lines.append(f"- {prefix}{fact['text']}")
    return '\n'.join(lines)


def search_memory(query, memory_dir):
    """
    语义检索 L2 记忆：embedding 优先，关键词 fallback。
    每次用户输入时调用，返回格式化的记忆文本可直接注入上下文。
    """
    # ── 路径 1: Embedding 语义检索 ──
    entries = _get_vectors(memory_dir)
    embedder = _get_embedder()
    if entries and embedder:
        try:
            from memory.vectors import search_vectors
            results = search_vectors(query, entries, embedder, top_k=5, threshold=0.4)
        except ImportError:
            results = []

        if results:
            lines = ['[Auto Retrieved Memory]']
            for r in results:
                prefix = f"[{r['section']}] " if r['section'] else ''
                lines.append(f"- {prefix}{r['key']}: {r['value']}")
            return '\n'.join(lines)

    # ── 路径 2: 关键词 fallback ──
    return _keyword_search(query, memory_dir)


def extract_facts(history_info, user_history, llmclient, memory_dir=None, timeout=20):
    """
    使用 LLM 从对话历史中提取持久性事实。
    在会话结束时调用。返回结构化事实列表。

    增量模式（默认）：用 embedding 检索与对话最相关的已有事实（top-20），
    只将这些传给 LLM 去重，避免 L2 膨胀时 token 爆炸。
    若 embedding 不可用，降级为传全部 L2。
    timeout: LLM 调用超时秒数（默认 20s）
    """
    conversation_lines = []
    for entry in history_info[-60:]:
        if isinstance(entry, dict):
            role = entry.get('role', '')
            if role != 'user':
                continue
            content = entry.get('content', '')
            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        texts.append(item.get('text', ''))
                content = ' '.join(texts)
            elif not isinstance(content, str):
                content = str(content)
            conversation_lines.append(f"[{role}]: {content}")
        elif isinstance(entry, str):
            conversation_lines.append(entry)
    for entry in user_history[-15:]:
        if isinstance(entry, str) and entry.startswith('[USER]'):
            conversation_lines.append(entry)

    conversation = '\n'.join(conversation_lines)
    if len(conversation.strip()) < 30:
        return []

    l2_path = os.path.join(memory_dir, 'global_mem.txt') if memory_dir else None

    # ── 增量模式（embedding 可用时）──
    existing_l2 = ''
    if l2_path and os.path.exists(l2_path):
        with open(l2_path, 'r', encoding='utf-8') as f:
            raw = f.read().strip()

        if raw and raw != '# [Global Memory - L2]':
            embedder = _get_embedder()
            if embedder is not None:
                # 用 embedding 检索 top-20 与对话最相关的已有事实
                relevant = _get_relevant_facts(conversation, memory_dir, top_k=20)
                if relevant:
                    existing_l2 = '\n'.join(relevant)
                    print(f"[Memory Auto] Incremental extraction: "
                          f"selected {len(relevant)} relevant facts from L2 "
                          f"(full L2 has ~{len(raw.split(chr(10)))} lines)")
                else:
                    existing_l2 = raw  # 检索失败，传全部
            else:
                existing_l2 = raw  # embedding 不可用，传全部

    existing_section = ''
    if existing_l2:
        existing_section = f"""=== 当前已存储的**相关**事实（必须复用已有 key 名！） ===
{existing_l2}
=== 结束 ===

严格规则：
- ★ 最关键 ★：如果对话中的新信息与已存储事实是**同一件事**，即使 key 名写法不同
  （如 "喜欢的颜色" 和 "颜色偏好"、"技术栈" 和 "编程语言"、"住址" 和 "所在地"），
  **必须复用已有的 key 名**！不要自创新 key！
- 如果对话中新信息与已存储事实 key 相同但 value 不同 → 输出新 value（视为更新）
- 如果对话中新信息与已存储事实完全一致 → 不要重复输出
- 如果对话中用户明确否定了旧偏好（如"不再喜欢黑色，现在喜欢白色"）→ 只输出新 value
"""

    extraction_prompt = f"""从以下对话中提取用户的持久性事实。只提取跨会话仍然成立的信息：

- 用户身份：姓名、性别、所在地、组织/公司、职位
- 用户偏好：颜色、食物、饮品、工具、语言、风格
- 环境配置：项目路径、工具路径、常用参数

不要提取：临时请求、一次性任务细节、当前会话特定内容。

输出纯 JSON 数组（不要 markdown 标记，key 必须用中文）：
[{{"section": "用户画像", "key": "颜色偏好", "value": "红色"}}]
如果没有值得持久化的事实，输出空数组：[]。

{existing_section}
=== 对话历史 ===
{conversation[-3500:]}
=== 结束 ===

JSON 输出："""

    extraction_prompt = f"""从以下对话中提取用户的持久性事实。只提取跨会话仍然成立的信息：

- 用户身份：姓名、性别、所在地、组织/公司、职位
- 用户偏好：颜色、食物、饮品、工具、语言、风格
- 环境配置：项目路径、工具路径、常用参数

不要提取：临时请求、一次性任务细节、当前会话特定内容。

输出纯 JSON 数组（不要 markdown 标记，key 必须用中文）：
[{{"section": "用户画像", "key": "颜色偏好", "value": "红色"}}]
如果没有值得持久化的事实，输出空数组：[]。

{existing_section}
=== 对话历史 ===
{conversation[-3500:]}
=== 结束 ===

JSON 输出："""

    messages = [
        {"role": "system", "content": "你是一个精确的事实提取系统。只输出 JSON 数组，不要 markdown，不要解释，不要多余文字。key 必须用中文。"},
        {"role": "user", "content": extraction_prompt}
    ]

    try:
        old_timeout = getattr(llmclient.backend, 'read_timeout', 120)
        llmclient.backend.read_timeout = min(old_timeout, timeout)
        try:
            gen = llmclient.backend.raw_ask(messages)
            response_parts = []
            for chunk in gen:
                if isinstance(chunk, str) and not chunk.startswith('!!!Error:') and not chunk.startswith('[Error:'):
                    response_parts.append(chunk)
            response = ''.join(response_parts)
        finally:
            llmclient.backend.read_timeout = old_timeout
    except Exception as e:
        print(f"[Memory Auto] Extraction LLM call failed: {e}")
        return []

    response = response.strip()
    json_match = re.search(r'\[.*\]', response, re.DOTALL)
    if not json_match:
        return []

    try:
        facts = json.loads(json_match.group())
        if isinstance(facts, list) and all(isinstance(f, dict) for f in facts):
            return facts
    except json.JSONDecodeError:
        pass

    return []


def _get_relevant_facts(conversation, memory_dir, top_k=20):
    """用 embedding 从 L2 中检索与对话最相关的已有事实（纯文本行）。
    用于增量萃取：只传相关事实给 LLM，避免 token 爆炸。
    """
    entries = _get_vectors(memory_dir)
    embedder = _get_embedder()
    if not entries or embedder is None:
        return []

    try:
        from memory.vectors import search_vectors
        results = search_vectors(
            conversation, entries, embedder,
            top_k=top_k, threshold=0.0  # 阈值放低，取够 top_k
        )
    except ImportError:
        return []

    # 还原为文本行格式
    lines = []
    for r in results:
        # 重建 section 头
        section = r.get('section', '')
        if section and section not in [l.strip('# []').strip() for l in lines]:
            lines.append(f'## [{section}]')
        lines.append(f'- {r["key"]}: {r["value"]}')
    return lines


def _sync_vectors(memory_dir):
    """同步 vectors.json 与 global_mem.txt"""
    embedder = _get_embedder()
    if embedder is None:
        return

    l2_path = os.path.join(memory_dir, 'global_mem.txt')
    vec_path = os.path.join(memory_dir, 'vectors.json')

    try:
        from memory.vectors import build_vectors, save_vectors
        entries = build_vectors(l2_path, embedder)
        save_vectors(entries, vec_path, l2_path, embedder)
        # 清除缓存，下次检索时重新加载
        global _vectors_cache
        _vectors_cache = None
    except ImportError:
        pass


def detect_conflicts(new_facts, existing_l2):
    """检测新事实与已有 L2 记忆的冲突"""
    conflicts = []
    for fact in new_facts:
        key = fact.get('key', '').strip()
        value = fact.get('value', '').strip()
        if not key or not value:
            continue
        pattern = re.compile(rf'^-\s*{re.escape(key)}\s*[:：]\s*(.+)$', re.MULTILINE | re.IGNORECASE)
        match = pattern.search(existing_l2)
        if match:
            old_value = match.group(1).strip().rstrip('(updated)').strip()
            if old_value.lower() != value.lower():
                conflicts.append({'key': key, 'old': old_value, 'new': value})
    return conflicts


def auto_update_l2(facts, memory_dir):
    """将提取的事实写入 L2 (global_mem.txt)，处理冲突和去重"""
    l2_path = os.path.join(memory_dir, 'global_mem.txt')

    existing = ''
    if os.path.exists(l2_path):
        with open(l2_path, 'r', encoding='utf-8') as f:
            existing = f.read()

    conflicts = detect_conflicts(facts, existing)

    # 也检测同一批次内的冲突（例如 setup→setup_2 的变更）
    for i, f1 in enumerate(facts):
        for f2 in facts[i + 1:]:
            k1, v1 = f1.get('key', ''), f1.get('value', '')
            k2, v2 = f2.get('key', ''), f2.get('value', '')
            if k1 and k2 and k1 == k2 and v1.lower() != v2.lower():
                conflicts.append({'key': k1, 'old': v1, 'new': v2})

    for c in conflicts:
        print(f"[Memory Auto] Conflict: '{c['key']}': '{c['old']}' → '{c['new']}'")

    added = 0
    updated = 0

    for fact in facts:
        key = fact.get('key', '').strip()
        value = fact.get('value', '').strip()
        section = fact.get('section', 'User Profile').strip()
        if not key or not value:
            continue

        # 检查是否已有完全相同的 key: value 行（去重）
        escaped_value = re.escape(value)
        dup_pattern = re.compile(
            rf'^-\s*{re.escape(key)}\s*[:：]\s*{escaped_value}\s*',
            re.MULTILINE | re.IGNORECASE
        )
        if dup_pattern.search(existing):
            continue  # 完全重复，跳过

        # Value 级去重: 检查是否已有其他 key 但 value 完全相同的行
        # 防止 LLM 自创了不同的 key 名来表达同一事实
        # （如已有的 "喜欢的颜色：卡其色" vs LLM 输出的 "颜色偏好: 卡其色"）
        escaped_value = re.escape(value)
        value_dup_pattern = re.compile(
            rf'^-\s*[^:：]+[:：]\s*{escaped_value}\s*$',
            re.MULTILINE | re.IGNORECASE
        )
        if value_dup_pattern.search(existing):
            print(f"[Memory Auto] Skipped (value duplicate with different key): '{key}: {value}'")
            continue

        # 检查是否已有相同 key 但不同 value（冲突）
        key_pattern = re.compile(rf'^-\s*{re.escape(key)}\s*[:：]', re.MULTILINE | re.IGNORECASE)
        is_conflict = any(c['key'] == key for c in conflicts)

        if key_pattern.search(existing) and is_conflict:
            c = next(c for c in conflicts if c['key'] == key)
            old_pattern = re.compile(
                rf'^(-\s*{re.escape(key)}\s*[:：]\s*)({re.escape(c["old"])})',
                re.MULTILINE | re.IGNORECASE
            )
            existing = old_pattern.sub(
                rf'\1{value}',
                existing,
                count=1
            )
            updated += 1
            print(f"[Memory Auto] Updated: '{key}': '{c['old']}' → '{value}'")
        elif not key_pattern.search(existing):
            section_header = f'## [{section}]'
            if section_header not in existing:
                existing += f'\n{section_header}\n'
            existing += f'- {key}: {value}\n'
            added += 1
        else:
            # 相同 key 不同 value，但未被识别为冲突 → 当作新事实添加
            section_header = f'## [{section}]'
            if section_header not in existing:
                existing += f'\n{section_header}\n'
            existing += f'- {key}: {value}\n'
            added += 1

    if added > 0 or updated > 0:
        with open(l2_path, 'w', encoding='utf-8') as f:
            f.write(existing)
        print(f"[Memory Auto] L2 updated: {added} new, {updated} updated")

        # ── 同步向量索引 ──
        _sync_vectors(memory_dir)
    else:
        print("[Memory Auto] No new persistable facts found")

    return added + updated
