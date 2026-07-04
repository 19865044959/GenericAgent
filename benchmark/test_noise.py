"""
验证关键词检索的噪音问题：
当 L2 有大量事实时，search_memory 会因关键词匹配缺乏区分度而拉入无关记忆。
"""

import os, sys
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from memory_auto import search_memory, auto_update_l2

MEMORY_DIR = os.path.join(SCRIPT_DIR, 'memory')
L2_PATH = os.path.join(MEMORY_DIR, 'global_mem.txt')

# ── 备份现有 L2 ──
if os.path.exists(L2_PATH):
    with open(L2_PATH, 'r', encoding='utf-8') as f:
        l2_backup = f.read()
else:
    l2_backup = ''

# ── 构造一个"重度用户"的 L2，模拟使用 2 周后的规模 ──
heavy_user_facts = [
    # 用户画像
    {"section": "用户画像", "key": "姓名", "value": "张三"},
    {"section": "用户画像", "key": "性别", "value": "男"},
    {"section": "用户画像", "key": "所在地", "value": "深圳"},
    {"section": "用户画像", "key": "公司", "value": "腾讯"},
    {"section": "用户画像", "key": "职位", "value": "后端开发"},
    {"section": "用户画像", "key": "工作年限", "value": "5年"},

    # 用户偏好（多个维度）
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

    # 环境配置
    {"section": "环境配置", "key": "操作系统", "value": "macOS"},
    {"section": "环境配置", "key": "Shell偏好", "value": "zsh"},
    {"section": "环境配置", "key": "Python版本", "value": "3.12"},
    {"section": "环境配置", "key": "包管理器偏好", "value": "uv"},

    # 技术栈
    {"section": "环境配置", "key": "主要语言", "value": "Python"},
    {"section": "环境配置", "key": "第二语言", "value": "Rust"},
    {"section": "环境配置", "key": "容器工具", "value": "Docker"},
    {"section": "环境配置", "key": "编排工具", "value": "Kubernetes"},
    {"section": "环境配置", "key": "CI/CD工具", "value": "GitHub Actions"},

    # 项目上下文（临时/可变）
    {"section": "项目上下文", "key": "当前项目", "value": "用户画像系统重构"},
    {"section": "项目上下文", "key": "上周任务", "value": "修复登录页面bug"},
    {"section": "项目上下文", "key": "下个迭代", "value": "接入新的embedding模型"},
]

# ── 写入 L2 ──
with open(L2_PATH, 'w', encoding='utf-8') as f:
    f.write('# [Global Memory - L2]\n')
auto_update_l2(heavy_user_facts, MEMORY_DIR)

# ── 读取最终的 L2 内容 ──
with open(L2_PATH, 'r', encoding='utf-8') as f:
    l2_content = f.read()

print("=" * 70)
print("L2 规模:", len(heavy_user_facts), "条事实")
print("L2 文件大小:", len(l2_content), "字符")
print("=" * 70)

# ── 测试用例 ──
test_queries = [
    "帮我推荐几件衣服",
    "最近想换个编辑器",
    "周末想出去吃点好的",
    "帮我写个 Python 脚本",
]

for query in test_queries:
    print(f"\n{'─' * 70}")
    print(f"Query: \"{query}\"")
    result = search_memory(query, MEMORY_DIR)
    if result:
        print(result)
        # 分析噪音
        print()
        lines = result.split('\n')
        facts_retrieved = [l for l in lines if l.startswith('- ')]
        print(f"  检索到 {len(facts_retrieved)} 条记忆")
    else:
        print("  (无匹配)")

# ── 恢复 L2 ──
with open(L2_PATH, 'w', encoding='utf-8') as f:
    f.write(l2_backup)

print(f"\n{'=' * 70}")
print("L2 已恢复")
