"""
vectors.py - 向量索引管理

向量文件 vectors.json 是 global_mem.txt 的机器可读缓存。
可通过 global_mem.txt 随时重建，不可手动编辑。

格式:
  {
    "l2_mtime": 1234567890.0,
    "model": "all-MiniLM-L6-v2",
    "dim": 384,
    "entries": [
      {
        "section": "用户偏好",
        "key": "颜色偏好",
        "value": "白色系",
        "embed_text": "[用户偏好] 颜色偏好: 白色系",
        "vector": [0.012, -0.034, ...]
      },
      ...
    ]
  }
"""

import os, json, re
import numpy as np


def _parse_l2_facts(l2_path: str) -> list[dict]:
    """解析 global_mem.txt，提取所有事实条目。

    Markdown 格式:
      # [Global Memory - L2]          — 文件标题（跳过）
      ## [Section]                     — 一级分类
      ### Subsection                   — 二级分类（可选）
      - key: value                     — 事实条目

    返回: [{"section": str, "subsection": str|None, "key": str, "value": str}, ...]
    """
    if not os.path.exists(l2_path):
        return []

    with open(l2_path, 'r', encoding='utf-8') as f:
        content = f.read()

    facts = []
    current_section = ''
    current_subsection = ''
    for line in content.split('\n'):
        stripped = line.strip()

        # H1: 文件标题，跳过
        if stripped.startswith('# ') and not stripped.startswith('## '):
            continue

        # H2: ## [SectionName]
        if stripped.startswith('## ['):
            current_section = stripped.strip('# []').strip()
            current_subsection = ''
            continue

        # H3: ### Subsection (optional)
        if stripped.startswith('### '):
            current_subsection = stripped[4:].strip()
            continue

        # Fact line: - key: value
        if stripped.startswith('- ') and len(stripped) > 2:
            text = stripped[2:].strip()
            if ':' in text or '：' in text:
                sep = ':' if ':' in text else '：'
                key, value = text.split(sep, 1)
                key = key.strip()
                value = value.strip()
                if key and value:
                    facts.append({
                        'section': current_section,
                        'subsection': current_subsection or None,
                        'key': key,
                        'value': value
                    })

    return facts


def fact_to_embed_text(fact: dict) -> str:
    """将一条事实转为 embedding 用文本。

    格式: "{key}: {value} [{section} > {subsection}]"
    key:value 放在前面主导语义，层级路径放在末尾当轻量标签。
    这样 "耳机偏好: 入耳式降噪 [购物偏好 > 数码电子产品]"
    和 "颜色偏好: 卡其色 [购物偏好 > 服装鞋帽]"
    的向量自然分开，不需硬编码关键词映射。
    """
    section = fact.get('section', '')
    subsection = fact.get('subsection', '')
    key = fact.get('key', '')
    value = fact.get('value', '')

    # 构建层级路径
    if subsection:
        hierarchy = f"{section} > {subsection}"
    elif section:
        hierarchy = section
    else:
        hierarchy = ''

    if hierarchy:
        return f"{key}: {value} [{hierarchy}]"
    return f"{key}: {value}"


def build_vectors(l2_path: str, embedder) -> list[dict]:
    """从 global_mem.txt 重建全部向量。

    Args:
        l2_path: global_mem.txt 路径
        embedder: Embedder 实例

    Returns:
        [{"section": str, "key": str, "value": str,
          "embed_text": str, "vector": list[float]}, ...]
    """
    facts = _parse_l2_facts(l2_path)
    if not facts:
        return []

    entries = []
    for fact in facts:
        text = fact_to_embed_text(fact)
        try:
            vec = embedder.encode(text)
            entries.append({
                'section': fact['section'],
                'subsection': fact.get('subsection'),
                'key': fact['key'],
                'value': fact['value'],
                'embed_text': text,
                'vector': vec.tolist()
            })
        except Exception as e:
            print(f"[Vectors] Failed to embed '{text}': {e}")

    return entries


def save_vectors(entries: list[dict], vec_path: str, l2_path: str,
                 embedder=None):
    """写入 vectors.json。

    如果 entries 为空但 embedder 可用，自动从 l2_path 重建。
    """
    if not entries and embedder is not None:
        entries = build_vectors(l2_path, embedder)

    l2_mtime = os.path.getmtime(l2_path) if os.path.exists(l2_path) else 0

    data = {
        'l2_mtime': l2_mtime,
        'model': getattr(embedder, 'backend_name', 'unknown') if embedder else 'unknown',
        'dim': embedder.dim if embedder else 0,
        'entries': entries
    }

    os.makedirs(os.path.dirname(vec_path), exist_ok=True)
    with open(vec_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"[Vectors] Saved {len(entries)} entries to {os.path.basename(vec_path)}")


def load_vectors(vec_path: str, embedder, l2_path: str) -> list[dict]:
    """加载 vectors.json。若 global_mem.txt 被修改过则自动重建。

    Returns:
        [{"section": str, "key": str, "value": str,
          "embed_text": str, "vector": np.ndarray}, ...]
        如果 embedder 不可用，返回空列表。
    """
    if embedder is None:
        return []

    l2_mtime = os.path.getmtime(l2_path) if os.path.exists(l2_path) else 0

    need_rebuild = True
    if os.path.exists(vec_path):
        try:
            with open(vec_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if (isinstance(data, dict) and
                data.get('l2_mtime') == l2_mtime and
                data.get('entries') is not None):
                need_rebuild = False
        except (json.JSONDecodeError, KeyError):
            pass

    if need_rebuild:
        print("[Vectors] Rebuilding index (L2 changed or no cache)...")
        entries = build_vectors(l2_path, embedder)
        save_vectors(entries, vec_path, l2_path, embedder)
    else:
        entries = data['entries']

    # 把 list 转回 numpy array 以便计算
    for entry in entries:
        entry['vector'] = np.array(entry['vector'], dtype=np.float32)

    return entries


def search_vectors(query: str, entries: list[dict], embedder,
                   top_k: int = 5, threshold: float = 0.4) -> list[dict]:
    """语义检索：返回 top_k 条相关性超过阈值的记忆。

    Args:
        query: 用户输入
        entries: load_vectors() 的返回值
        embedder: Embedder 实例
        top_k: 最大返回条数
        threshold: 余弦相似度阈值 (0~1)

    Returns:
        [{"section": str, "key": str, "value": str, "score": float}, ...]
        按 score 降序排列
    """
    if embedder is None or not entries:
        return []

    query_vec = embedder.encode(query)

    from .embedder import cosine_similarity

    scored = []
    for entry in entries:
        score = cosine_similarity(query_vec, entry['vector'])
        if score >= threshold:
            scored.append({
                'section': entry['section'],
                'subsection': entry.get('subsection'),
                'key': entry['key'],
                'value': entry['value'],
                'score': float(score)
            })

    scored.sort(key=lambda x: -x['score'])
    return scored[:top_k]
