"""
对比关键词检索 vs Embedding 语义检索

场景: 31 条事实的"重度用户"L2，测试 4 个 query 的检索质量。
"""

import os, sys
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

# Force ONNX backend before memory_auto caches NgramFallback
import memory.embedder as _emb
try:
    _emb._embedder_instance = _emb.OnnxEmbedder()
    print(f"[Test] Forcing ONNX backend")
except (ImportError, FileNotFoundError) as e:
    print(f"[Test] ONNX not available ({e}), using default backend")

import memory_auto as ma_module
search_memory = ma_module.search_memory        # 新版：纯 embedding（关键词 fallback）
_keyword_search = ma_module._keyword_search    # 旧版：纯关键词
auto_update_l2 = ma_module.auto_update_l2

from memory.embedder import get_embedder

MEMORY_DIR = os.path.join(SCRIPT_DIR, 'memory')
L2_PATH = os.path.join(MEMORY_DIR, 'global_mem.txt')
VEC_PATH = os.path.join(MEMORY_DIR, 'vectors.json')

# ── 备份 ──
l2_backup = ''
if os.path.exists(L2_PATH):
    with open(L2_PATH, 'r', encoding='utf-8') as f:
        l2_backup = f.read()

# 清空旧向量缓存
if os.path.exists(VEC_PATH):
    os.remove(VEC_PATH)

# ── 构建 31 条事实 ──
facts = [
    {"section": "用户画像", "key": "姓名", "value": "张三"},
    {"section": "用户画像", "key": "性别", "value": "男"},
    {"section": "用户画像", "key": "所在地", "value": "深圳"},
    {"section": "用户画像", "key": "公司", "value": "腾讯"},
    {"section": "用户画像", "key": "职位", "value": "后端开发"},
    {"section": "用户画像", "key": "工作年限", "value": "5年"},
    {"section": "用户偏好", "key": "颜色偏好", "value": "白色系"},
    {"section": "用户偏好", "key": "风格偏好", "value": "简约风格"},
    {"section": "用户偏好", "key": "饮食偏好", "value": "川菜"},
    {"section": "用户偏好", "key": "饮品偏好", "value": "手冲咖啡"},
    {"section": "用户偏好", "key": "音乐偏好", "value": "古典音乐"},
    {"section": "用户偏好", "key": "电影偏好", "value": "科幻片"},
    {"section": "用户偏好", "key": "运动偏好", "value": "跑步"},
    {"section": "用户偏好", "key": "阅读偏好", "value": "技术博客"},
    {"section": "用户偏好", "key": "编辑器偏好", "value": "VSCode"},
    {"section": "用户偏好", "key": "IDE偏好", "value": "Cursor"},
    {"section": "用户偏好", "key": "终端偏好", "value": "Warp"},
    {"section": "用户偏好", "key": "AI工具偏好", "value": "Claude"},
    {"section": "用户偏好", "key": "数据库偏好", "value": "PostgreSQL"},
    {"section": "环境配置", "key": "操作系统", "value": "macOS"},
    {"section": "环境配置", "key": "Shell偏好", "value": "zsh"},
    {"section": "环境配置", "key": "Python版本", "value": "3.12"},
    {"section": "环境配置", "key": "包管理器偏好", "value": "uv"},
    {"section": "环境配置", "key": "主要语言", "value": "Python"},
    {"section": "环境配置", "key": "第二语言", "value": "Rust"},
    {"section": "环境配置", "key": "容器工具", "value": "Docker"},
    {"section": "环境配置", "key": "编排工具", "value": "Kubernetes"},
    {"section": "环境配置", "key": "CI/CD工具", "value": "GitHub Actions"},
    {"section": "项目上下文", "key": "当前项目", "value": "用户画像系统重构"},
    {"section": "项目上下文", "key": "上周任务", "value": "修复登录页面bug"},
    {"section": "项目上下文", "key": "下个迭代", "value": "接入新的embedding模型"},
]

# 写入 L2
with open(L2_PATH, 'w', encoding='utf-8') as f:
    f.write('# [Global Memory - L2]\n')
auto_update_l2(facts, MEMORY_DIR)

# 读取 L2 行数
with open(L2_PATH, 'r', encoding='utf-8') as f:
    l2_lines = len(f.read().split('\n'))

print("=" * 70)
print(f"L2: {len(facts)} 条事实, {l2_lines} 行")
embedder = get_embedder()
print(f"Embedding 后端: {embedder.backend_name if embedder else '不可用 (降级为关键词)'}")
print("=" * 70)

# ── 测试用例 ──
test_cases = [
    {
        "query": "帮我推荐几件衣服",
        "desc": "衣服推荐 → 应只召回颜色/风格偏好",
        "expected": ["颜色", "风格", "白色", "简约"],
        "noise": ["饮食", "饮品", "音乐", "电影", "运动", "数据库", "编辑器"],
    },
    {
        "query": "最近 deadline 快到了，好焦虑",
        "desc": "焦虑 → 应召回音乐偏好（压力大时听古典音乐放松）",
        "expected": ["音乐", "古典"],
        "noise": [],
    },
    {
        "query": "帮我写个 Python 脚本",
        "desc": "Python 脚本 → 应只召回技术相关",
        "expected": ["Python", "主要语言"],
        "noise": ["颜色", "饮食", "音乐", "电影"],
    },
    {
        "query": "最近想换个编辑器",
        "desc": "编辑器 → 应只召回编辑器相关",
        "expected": ["编辑器", "VSCode", "IDE", "Cursor"],
        "noise": ["颜色", "饮食", "饮品", "音乐"],
    },
]

total_keyword_noise = 0
total_keyword_relevant = 0
total_embed_noise = 0
total_embed_relevant = 0

for tc in test_cases:
    query = tc["query"]
    print(f"\n{'─' * 70}")
    print(f"Query: \"{query}\"")
    print(f"意图: {tc['desc']}")

    # ── 关键词检索 ──
    kw_result = _keyword_search(query, MEMORY_DIR)
    print(f"\n  [关键词检索]")
    if kw_result:
        for line in kw_result.split('\n')[1:]:
            is_noise = any(n in line for n in tc['noise'])
            is_expected = any(e in line for e in tc['expected'])
            marker = "  ❌ 噪音" if is_noise and not is_expected else ("  ✅" if is_expected else "  ?")
            print(f"    {marker} {line}")
            if is_noise and not is_expected:
                total_keyword_noise += 1
            if is_expected:
                total_keyword_relevant += 1
    else:
        print("    (无匹配)")

    # ── Embedding 检索 ──
    emb_result = search_memory(query, MEMORY_DIR)  # 含 cache 清除
    ma_module._vectors_cache = None
    emb_result = search_memory(query, MEMORY_DIR)

    print(f"\n  [Embedding 检索]")
    if emb_result:
        for line in emb_result.split('\n')[1:]:
            is_noise = any(n in line for n in tc['noise'])
            is_expected = any(e in line for e in tc['expected'])
            marker = "  ❌ 噪音" if is_noise and not is_expected else ("  ✅" if is_expected else "  ?")
            print(f"    {marker} {line}")
            if is_noise and not is_expected:
                total_embed_noise += 1
            if is_expected:
                total_embed_relevant += 1
    else:
        print("    (无匹配)")

# ── 汇总 ──
print(f"\n{'=' * 70}")
print("汇总对比")
print("=" * 70)

if total_keyword_relevant + total_keyword_noise > 0:
    kw_noise_rate = total_keyword_noise / (total_keyword_relevant + total_keyword_noise) * 100
else:
    kw_noise_rate = 0

if total_embed_relevant + total_embed_noise > 0:
    emb_noise_rate = total_embed_noise / (total_embed_relevant + total_embed_noise) * 100
else:
    emb_noise_rate = 0

print(f"  关键词检索: {total_keyword_relevant} 条相关 + {total_keyword_noise} 条噪音"
      f"  (噪音率 {kw_noise_rate:.0f}%)")
print(f"  Embedding:  {total_embed_relevant} 条相关 + {total_embed_noise} 条噪音"
      f"  (噪音率 {emb_noise_rate:.0f}%)")

if embedder:
    improvement = kw_noise_rate - emb_noise_rate
    print(f"\n  噪音降低: {improvement:.0f} 个百分点")

# ── 恢复 ──
with open(L2_PATH, 'w', encoding='utf-8') as f:
    f.write(l2_backup)
if os.path.exists(VEC_PATH):
    os.remove(VEC_PATH)
ma_module._vectors_cache = None
print("\nL2 已恢复")
