"""可复现: 验证为什么"猫"的embedding分数 > "颜色偏好" """
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory.embedder import OnnxEmbedder
import numpy as np

emb = OnnxEmbedder()
query = '帮我推荐几件适合我的衣服'
q_vec = emb.encode(query)

# EMB-04 的完整 L2
facts = {
    '颜色偏好': '[用户偏好] 颜色偏好: 卡其色和白色系',
    '风格偏好': '[用户偏好] 风格偏好: 简约日系风格',
    '饮食偏好': '[用户偏好] 饮食偏好: 川菜',
    '饮品偏好': '[用户偏好] 饮品偏好: 手冲咖啡',
    '音乐偏好': '[用户偏好] 音乐偏好: 古典音乐',
    '电影偏好': '[用户偏好] 电影偏好: 科幻片',
    '运动偏好': '[用户偏好] 运动偏好: 跑步',
    '宠物偏好': '[用户偏好] 宠物偏好: 猫',
    '所在地':   '[用户画像] 所在地: 杭州',
    '职位':     '[用户画像] 职位: 前端开发',
    '技术':     '[环境配置] 技术: TypeScript',
    '操作系统': '[环境配置] 操作系统: macOS',
    '书籍偏好': '[环境配置] 书籍偏好: 科幻小说',
    '旅行偏好': '[环境配置] 旅行偏好: 海边城市',
    '汽车偏好': '[环境配置] 汽车偏好: SUV',
    '游戏偏好': '[环境配置] 游戏偏好: 策略类',
    'IDE偏好':  '[环境配置] IDE偏好: VSCode',
    '季节偏好': '[环境配置] 季节偏好: 秋天',
    '社交偏好': '[环境配置] 社交偏好: 小圈子聚会',
}

print(f'Model: {emb.backend_name}')
print(f'Query: "{query}"')
print(f'{"=" * 60}')

scored = []
for name, text in facts.items():
    score = float(np.dot(q_vec, emb.encode(text)))
    scored.append((score, name, text))

scored.sort(reverse=True)

print(f'{"Fact":<20s} {"Score":>8s}  Verdict')
print(f'{"-" * 60}')
for score, name, text in scored:
    mark = '✅ TOP-5' if score >= 0.40 else '❌'
    print(f'{name:<20s} {score:>8.4f}  {mark}')

print(f'{"=" * 60}')
print()

# ── 深挖 ──
print('=== 深挖: 为什么猫(0.43) > 颜色(0.40)? ===')
print()

print('1. 裸词 vs query 的相似度:')
for word in ['猫', '衣服', '卡其色', '白色系', '跑步', '川菜', '秋天', '简约']:
    s = float(np.dot(q_vec, emb.encode(word)))
    print(f'   "{word}"         → {s:.4f}')

print()
print('2. 去掉 section 前缀:')
print(f'   "宠物偏好: 猫"           → {float(np.dot(q_vec, emb.encode("宠物偏好: 猫"))):.4f}')
print(f'   "颜色偏好: 卡其色和白色系" → {float(np.dot(q_vec, emb.encode("颜色偏好: 卡其色和白色系"))):.4f}')
print(f'   "颜色: 卡其色和白色系"     → {float(np.dot(q_vec, emb.encode("颜色: 卡其色和白色系"))):.4f}')
print('   → value 越短越聚焦，越不容易被稀释')

print()
print('3. 为什么 BGE 认为猫和衣服有关?')
print('   BGE 训练语料中，猫主题服饰(猫耳帽/猫咪T恤/优衣库猫系列)')
print('   高频共现，模型学到了"猫"和"服饰"的文化关联。')
print('   这是真实的语义捕捉，不是bug——只是不该出现在这个场景。')
