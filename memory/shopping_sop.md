# Shopping SOP — 电商购物 Agent Loop

**触发词**：帮我买/想买/下单/推荐/比价/挑/选/哪个好/划算/逛逛/看看/有没有/搜一下 + 商品相关

L3 memory: shopping_sop.md | 依赖: tmwebdriver_sop (浏览器操作), ljqCtrl_sop (键鼠)

### ⚡ 浏览器选择快速指南（2026-07-04 实测更新）

| 场景 | 推荐浏览器 | 原因 |
|:---|:---|:---|
| **淘宝/天猫购物** | ⭐ **Edge**（优先） | 对淘宝 React 组件兼容更好，SKU 选择/价格显示更稳定 |
| 京东购物 | Edge ≥ Chrome | 两者均可，Edge 登录态保持更好 |
| 拼多多 | 均可 | 无显著差异 |
| 搜商品/信息检索 | 均可 | 无特殊要求 |
| 其他平台（唯品会/得物等） | Chrome | 通用兼容性 |

**坑：** Chrome 在淘宝普通店铺（非天猫）偶发 React 组件渲染不全，SKU 面板不响应 click 事件，需降级用 `new MouseEvent('click')` 发送事件。

---

## 核心原则

⛔ **用户永远拥有最终决策权** — Agent 只做信息聚合、比较、推荐，不自行下单
⛔ **每轮必须有用户可见的输出** — 呈现选项、差异表或进度，禁止黑盒操作
⛔ **偏好驱动搜索** — 每轮 SEARCH 前检查 L2 记忆，用户偏好注入搜索条件
⛔ **收敛优先** — 每轮必须缩小候选集，连续 2 轮未收敛 → 主动问用户缺什么条件
⛔ **透明比较** — 呈现选项必须带关键差异维度（价格/品牌/参数/评价），不能只列名字
⛔ **风险必报** — 极低价、新店、0 评价、差评集中 → 明确警告用户
⛔ **金额敏感** — 超过用户 L2 历史均价 2x 或 >5000 元时，提示但不阻止
⛔ **不碰支付** — 支付密码/指纹/人脸验证必须由用户完成，Agent 只做到结算确认页

---

## 购物 Agent Loop 流程图

```
                        ┌─────────────────────┐
                        │   用户表达购物意图    │
                        └──────────┬──────────┘
                                   │
                        ┌──────────▼──────────┐
                        │  TURN 0: CLARIFY    │
                        │  注入L2记忆 + 意图分类│
                        │  提取约束条件        │
                        │  缺关键信息→问用户   │
                        └──────────┬──────────┘
                                   │ 约束足够
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
       明确购买意图          模糊探索意图          比价查询意图
      ("买X"/"下单X")     ("看看X"/"逛逛")    ("X和Y哪个好"/"划算")
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
                        ┌──────────▼──────────┐
                        │    SEARCH            │  ← 进入迭代循环
                        │  web_scan 导航+搜索   │
                        │  收集候选商品列表     │
                        └──────────┬──────────┘
                                   │
                        ┌──────────▼──────────┐
                        │    FILTER            │
                        │  去重/筛除不相关      │
                        │  按销量+评价初排      │
                        │  候选>5→加筛选条件   │
                        └──────────┬──────────┘
                                   │
                        ┌──────────▼──────────┐
                        │    PRESENT           │
                        │  呈现Top3-5+差异表   │
                        │  标注关键差异维度     │
                        │  → ask_user 确认方向 │
                        └──────────┬──────────┘
                                   │
                        ┌──────────▼──────────┐
                        │    REFINE            │
                        │  解析用户反馈         │
                        │  更新约束条件         │
                        │  候选收敛？           │
                        └──────────┬──────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │ 是(≤3个)     │ 否           │ 超过5轮
                    ▼              ▼              ▼
             DEEP_EVAL      回到 SEARCH     强制进入
            (深度评估)      (缩小范围)      DEEP_EVAL
                    │                              
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    读详细评价   查卖家信誉   价格历史
        │           │           │
        └───────────┼───────────┘
                    │
        ┌───────────▼───────────┐
        │       DECIDE           │
        │  呈现1-2最佳选项+理由  │
        │  标注风险+Trade-off   │
        │  → ask_user 确认购买   │
        └───────────┬───────────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       "买X"    "再看看"   "不买了"
          │         │         │
          │    回到PRESENT  记录偏好
          │    换角度呈现    退出
          │
  ┌───────▼───────┐
  │    EXECUTE     │
  │  确认规格+地址 │
  │  确认优惠券    │
  │  加购→结算     │
  │  用户自己支付   │
  └───────┬───────┘
          │
  ┌───────▼───────┐
  │   POST-PURCHASE│
  │  记录订单信息   │
  │  提示跟踪物流   │
  │  会话结束→萃取  │
  │  偏好到L2      │
  └───────────────┘
```

---

## 状态机

### 状态定义

| 状态 | 说明 | 进入条件 | 退出条件 |
|:---|:---|:---|:---|
| **CLARIFY** | 需求澄清 | 用户表达购物意图 | 约束足够具体（品类+至少1个其他维度） |
| **SEARCH** | 执行搜索 | 约束已明确 | 收集到 ≥5 个候选或确认结果不足 |
| **FILTER** | 筛选排序 | 候选池已建立 | 候选 ≤5 个且都是相关商品 |
| **PRESENT** | 呈现选项 | 候选集已过滤好 | 用户给出明确反馈（偏好方向/否定/新条件） |
| **REFINE** | 收敛判断 | 用户反馈已收集 | 候选 ≤3 → DEEP_EVAL；候选>3 → SEARCH |
| **DEEP_EVAL** | 深度评估 | 候选收敛到 2-3 个 | 评估完成，有明确推荐排序 |
| **DECIDE** | 最终推荐 | 深度评估完成 | 用户说"买"→EXECUTE；"不买"→退出 |
| **EXECUTE** | 下单执行 | 用户确认购买 | 订单提交成功 |
| **TRACK** | 售后跟踪 | 下单完成 | 用户说不需要/确认收货 |

### 收敛控制（硬约束）

```
最大搜索轮次: SEARCH → PRESENT → REFINE → SEARCH 循环最多 5 轮
收敛死锁检测: 连续 2 轮候选集大小不变 → 主动问用户"我是不是漏掉了什么条件？"
强制收敛: 第 5 轮后仍 >3 个候选 → 直接用价格+销量+评分加权排序，Top 2 进入 DEEP_EVAL

每轮输出格式:
  [本轮约束] 品类=XX | 预算=XX | 品牌偏好=XX | 其他=XX
  [候选数量] 搜索到N个 → 筛选后M个
  [新增约束] 用户反馈提取到的约束变化
```

---

## 关键决策门 (Decision Gates)

### Gate 1: 用户意图分类（CLARIFY 阶段）

```
用户输入 → 分类:
├─ "买"/"下单"/"帮我买" + 具体商品      → INTENT_BUY     → 直接 SEARCH
├─ "想看看"/"逛逛"/"有什么"/"推荐"      → INTENT_EXPLORE  → 宽泛搜索，2轮探索后问用户缩小
├─ "A和B哪个好"/"对比"/"划算"/"值得买"  → INTENT_COMPARE  → 直奔比价+评价对比，跳过探索
└─ 纯闲聊/非购物                        → 不触发本SOP
```

### Gate 2: 约束完整性检查（CLARIFY → SEARCH）

```
必须维度:
  品类      ✓ 所有购物都需要
  预算范围   ? 不明确时先用 L2 历史预算，没有则给三个档位让用户选
  品牌偏好   ? 不明确时先不限制，第一轮呈现后根据反馈补充
  时间要求   ? 默认不急，除非用户提"急用"/"今天要"
  关键属性   ? 因品类而异（衣服:尺码风格 电子:参数 食品:口味）

缺预算:
  → "你的预算大概在什么范围？我可以按 (a)200以下 (b)200-500 (c)500-1000 (d)1000以上 帮你筛选"
缺品类细节（"想买个电子产品"）:
  → "电子产品范围很广，你更倾向: (a)手机 (b)平板 (c)笔记本 (d)耳机 (e)其他？"
```

### Gate 3: 是否问用户？（全流程通用）

```
涉及主观偏好（颜色/款式/手感/口味）     → ⛔ 必须问，Agent 无法替用户感受
涉及客观事实（参数/价格/评价/销量）     → Agent 自己查，不问用户
涉及安全风险（极低价/新店/0评价/差评多） → ⛔ 必须主动警告，不等用户问
涉及大额支出（>L2历史均价2x 或 >5000元）→ 提示用户注意，但不必阻止
纯操作步骤（翻页/加购/切换tab）         → Agent 自己执行，不问用户
```

### Gate 4: 下单确认（DECIDE → EXECUTE）

```
⛔ 必须确认的项目（缺一不可）:
  1. 商品链接/名称        — 防止买错
  2. 规格（颜色/尺码/型号）— 防止选错 SKU
  3. 收货地址              — 从 L2 读取，首次或无记录必须问
  4. 优惠券/满减           — Agent 主动检查可用优惠
  5. 最终价格              — 明确显示含运费的到手价

确认格式:
  ask_user(
    question="确认下单以下商品？",
    candidates=[
      "✅ 确认下单",
      "❌ 我再想想",
      "🔄 换一家店/换个规格"
    ]
  )

⛔ 支付密码/指纹/人脸 — Agent 禁止参与，让用户自己完成
```

---

## 工具映射

### GenericAgent 9 原子工具 → 购物场景

| 购物步骤 | GA 工具 | 具体用法 |
|:---|:---|:---|
| 打开电商网站 | `web_scan` | 导航到平台首页（淘宝/京东/拼多多），获取页面结构 |
| 搜索商品 | `web_execute_js` | 定位搜索框 → 输入关键词 → 触发搜索事件 |
| 提取搜索结果列表 | `web_scan` | 获取简化 HTML，提取商品名/价格/销量/店铺名 |
| 翻页 | `web_execute_js` | 点击下一页或滚动加载更多 |
| 按条件筛选 | `web_execute_js` | 点击筛选标签（价格区间、品牌、销量排序、好评率） |
| 进入商品详情页 | `web_execute_js` | 点击商品卡片，切换到详情页 tab |
| 读取商品详情 | `web_scan` | 获取详情页 HTML，提取规格参数、价格、库存 |
| 读取评价 | `web_scan` + `code_run` | 切换评价tab → web_scan 抓取 → Python 汇总分析 |
| 跨平台比价 | `code_run` | Python 整理多平台价格+参数对比表 |
| 保存候选列表 | `update_working_checkpoint` | 持久化候选商品列表，跨轮次保持 |
| 询问用户 | `ask_user` | 关键决策节点（选方向、确认购买、确认地址） |
| 记录订单 | `file_write` | 下单成功后写入 `temp/order_record.json` |
| 注入用户偏好 | `search_memory` | agentmain 自动调用，无需手动触发 |

### web_execute_js 常用操作模板

```javascript
// 搜索（以淘宝为例，通用模式）
const input = document.querySelector('input[type="search"], input.search-input, input[name="q"]');
input.value = '搜索关键词';
input.dispatchEvent(new Event('input', {bubbles: true}));
// 触发搜索
const btn = document.querySelector('button[type="submit"], .search-btn, form button');
btn.click();

// 按销量排序
const sortBtn = Array.from(document.querySelectorAll('*'))
  .find(el => el.textContent.includes('销量'));
if (sortBtn) sortBtn.click();

// 设置价格区间
// 先找到价格筛选区域，再填入 min/max

// 提取当前页商品列表
JSON.stringify(Array.from(document.querySelectorAll('[data-spm], .item, .card'))
  .slice(0, 20)
  .map(el => ({
    title: el.querySelector('.title, .name, h3')?.textContent?.trim(),
    price: el.querySelector('.price, .price-now')?.textContent?.trim(),
    shop: el.querySelector('.shop, .seller')?.textContent?.trim(),
    sales: el.querySelector('.sales, .deal')?.textContent?.trim(),
    link: el.querySelector('a')?.href
  })));
```

---

## 记忆系统集成

### L1 — 索引（本 SOP 注册）
在 `global_mem_insight.txt` 的 L3 行中加入本 SOP 的触发词。

### L2 — 购物偏好自动沉淀

每次购物会话结束时，`memory_auto.extract_facts()` 自动萃取：

```
## [购物偏好]
- 常用平台: 京东
- 预算范围: 数码产品 3000-5000元, 衣服 200-500元
- 品牌偏好: 小米、Apple、优衣库
- 风格偏好: 简约、纯色
- 尺码: M / 42码
- 配送地址: 北京市朝阳区xxx
- 支付偏好: 支付宝/微信
```

### L2 注入时机

- **TURN 0 CLARIFY 时**：`search_memory()` 自动检索相关偏好，注入到上下文
- **SEARCH 前**：检查是否已有同品类购物历史（如上次买的同品类预算区间）
- **DECIDE 前**：检查配送地址是否已在 L2，无则询问

### L4 — 会话归档

购物会话完整记录保存在 `memory/L4_raw_sessions/`，供后续分析购物习惯和偏好变化趋势。

---

## EXECUTE 阶段：连浏览器 → 下单 逐步骤操作手册（天猫真实案例）

> 本手册以**真维斯纯棉短袖T恤（卡其色/XL，¥27.9）**的下单过程为案例，完整还原每一步的具体代码、工具、指令、以及踩坑点。
> 假设场景：用户已确认购买，商品详情页已在 Edge 中打开，当前在 **DECIDE** 阶段。

---

### 步骤 0：前置环境检查（一定要先做！）

**做什么：** 确认 Edge 浏览器已打开且有调试端口 9222

**用到的工具：** `code_run`（bash/python）

**指令：**
```bash
# 检查 Edge 进程是否存在
powershell.exe -NoProfile -Command "Get-Process msedge -ErrorAction SilentlyContinue | Select-Object Id, ProcessName"

# 检查调试端口 9222 是否在监听
powershell.exe -NoProfile -Command "netstat -ano | findstr ':9222'"
```

**📛 踩坑记录 — 为什么必须做这步：**
> ❌ **坑 1**：WSL 下直接 `subprocess.run(['cmd.exe', '/c', 'start', 'msedge', URL])` → **永远超时**。WSL 的 cmd start 命令在后台挂起不返回。
> ✅ **正确**：用 PowerShell 的 Start-Process 或检查现有进程。
>
> ❌ **坑 2**：直接 `subprocess.run(['msedge', URL])` → 找不到路径，因为 Edge 在 `C:\Program Files (x86)\Microsoft\Edge\Application\` 但 WSL 不自动解析 PATH。
> ✅ **正确**：先用 Get-Process 探测浏览器是否已在运行。

**判断标准：**
- 如果有 Edge 进程 + 9222 在 LISTENING → **跳步骤 1**（直接连现有浏览器）
- 如果 Edge 有进程但 9222 没监听 → 用 Selenium 启动带调试端口的 Edge
- 如果 Edge 没开 → 用 PowerShell 打开淘宝商品页：
  ```powershell
  powershell.exe -NoProfile -Command "Start-Process 'msedge' 'https://detail.tmall.com/item.htm?id=商品ID'"
  ```

---

### 步骤 1：编写 Selenium 脚本

**做什么：** 把 Python 脚本写到 `temp/selenium_order.py`

**用到的工具：** `code_run`（python type），产出文件用写文件操作

**📛 踩坑记录 — Python 环境问题：**
> ❌ **坑 3**：WSL 的 `/usr/bin/python3` 没有装 selenium（会报 `ModuleNotFoundError`）。
> ✅ **正确**：用 Windows 的 Python314 环境，路径固定为：
> ```
> C:\Users\zhanghuanyu\AppData\Local\Programs\Python\Python314\python.exe
> ```
> 该环境已安装 selenium 4.45.0。
>
> ❌ **坑 4**：WSL 路径 `/mnt/d/work/...` 传到 Windows PowerShell 变成 `D:\mnt\d\work\...` → 文件找不到。
> ✅ **正确**：传路径时必须用 Windows 格式 `D:\work\Hackthon\GenericAgent\temp\script.py`。

**编写的脚本内容（完整可运行）：**
```python
import sys, time
sys.stdout.reconfigure(encoding='utf-8')

from selenium import webdriver
from selenium.webdriver.edge.options import Options

# ============================================================
# 步骤 1.1：连现有 Edge（保留登录态）
# ============================================================
options = Options()
options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
driver = webdriver.Edge(options=options)

# ============================================================
# 步骤 1.2：找到商品详情页的标签页
# ============================================================
product_url_marker = 'item?id=892828389189'  # ← 替换为你的商品ID
target_tab = None
for h in driver.window_handles:
    driver.switch_to.window(h)
    if product_url_marker in driver.current_url:
        target_tab = h
        break

if not target_tab:
    # 如果商品页没打开，导航过去
    driver.get("https://detail.tmall.com/item.htm?id=892828389189")
    time.sleep(3)

print(f"当前页面: {driver.title}")
print(f"当前URL: {driver.current_url}")

# ============================================================
# 步骤 1.3：检查 SKU 选择状态
# ============================================================
state = driver.execute_script("""
    let selected = [];
    document.querySelectorAll('.selected').forEach(el => {
        let t = (el.textContent || '').trim();
        if(t.length > 0 && t.length < 20) selected.push(t);
    });
    return JSON.stringify({selected: selected, url: window.location.href});
""")
print(f"SKU 选中状态: {state}")

# ============================================================
# 步骤 1.4：选择颜色/款式（先点颜色，必须等 DOM 更新）
# ============================================================
print("\n步骤 1.4: 选择卡其色...")
result_color = driver.execute_script("""
    // 用文本匹配查找，因为天猫 CSS Modules class 每次构建会变
    const spans = document.querySelectorAll('span');
    for(let s of spans) {
        let t = s.textContent.trim();
        if((t === '卡其色' || t.startsWith('卡其')) && s.offsetParent !== null) {
            s.click();
            return '点击了: ' + t;
        }
    }
    return '未找到卡其色';
""")
print(result_color)
time.sleep(1.5)  # ⚠ 必须等！React 组件需要时间更新状态

# ============================================================
# 步骤 1.5：选择尺码（再点尺码，再等 DOM 更新）
# ============================================================
print("\n步骤 1.5: 选择 XL...")
result_size = driver.execute_script("""
    const all = document.querySelectorAll('div, span, label');
    for(let el of all) {
        let t = el.textContent.trim();
        if((t === 'XL' || t === 'XL ') && el.offsetParent !== null) {
            el.click();
            return '点击了: XL';
        }
    }
    return '未找到 XL';
""")
print(result_size)
time.sleep(1.5)  # ⚠ 必须等！

# ============================================================
# 步骤 1.6：再次检查 SKU 是否真正选中
# ============================================================
state2 = driver.execute_script("""
    let selected = [];
    document.querySelectorAll('.selected').forEach(el => {
        let t = (el.textContent || '').trim();
        if(t.length > 0 && t.length < 20) selected.push(t);
    });
    return JSON.stringify({selected: selected, url: window.location.href});
""")
print(f"选中后状态: {state2}")

# ============================================================
# 步骤 1.7：点击"领券购买"按钮
# ============================================================
print("\n步骤 1.7: 点击领券购买...")
result_buy = driver.execute_script("""
    // 找包含"领券购买"文本的 span，然后向上找到可点击的容器
    const spans = document.querySelectorAll('span');
    for(let s of spans) {
        if(s.textContent.includes('领券购买')) {
            // 向上找到 class 含 btnItem 的父 DIV
            let btn = s.closest('[class*="btnItem"]');
            if(btn) {
                btn.click();
                return '已点击领券购买按钮';
            }
        }
    }
    // 备用：直接找 class 含 btnItem 的元素
    let fallback = document.querySelector('[class*="btnItem"]');
    if(fallback) {
        fallback.click();
        return '备用方案: 点击了btnItem';
    }
    return '未找到领券购买按钮';
""")
print(result_buy)

# 等待页面跳转
time.sleep(3)

# ============================================================
# 步骤 1.8：检查是否跳转到确认订单页
# ============================================================
final_url = driver.execute_script("return window.location.href;")
final_title = driver.title
print(f"\n最终URL: {final_url}")
print(f"最终标题: {final_title}")

if 'buy.tmall.com/order/confirm_order' in final_url:
    print("\n✅ 下单成功！已到达确认订单页")
else:
    print("\n❌ 未跳转到确认页，检查原因")

driver.quit()
```

**📛 踩坑记录 — Selenium 脚本中的关键注意点：**
> ❌ **坑 5**：天猫的 class 名是 CSS Modules 生成的（如 `btnItem--NstK3Os1`），每次构建都变。**禁止硬编码完整 class 名**。应该用：
>  - `[class*="btnItem"]` — 部分匹配
>  - 或 `el.closest('[class*="btnItem"]')` — 从文本元素向上找
>  - 或直接用文本包含匹配：`s.textContent.includes('领券购买')`
>
> ❌ **坑 6**：不要用 `dispatchEvent(new MouseEvent('click'))` — React 可能不响应合成事件。直接用 `element.click()`。
>
> ❌ **坑 7**：天猫的"领券购买"不是一个 `<a>` 标签，也不是 `<button>`，而是**多层 DIV 嵌套**。所以不能通过找 `a[href]` 或构造链接绕过，必须真实点击 DOM 元素。
>
> ❌ **坑 8**：URL 里带了 `sku_properties=20509%3A28317` **不等于**商品 SKU 已经在 UI 上被选中。页面加载后需要执行 selected 检查，如果发现 `.selected` 列表为空，必须手动点。
>
> ❌ **坑 9**：不能同时点颜色和尺码（在一个 JS execute_script 里循环点两个）。必须**先点颜色 → time.sleep(1.5) → 再点尺码**。跳过了间隔会导致 React 组件状态不同步。

---

### 步骤 2：执行脚本

**做什么：** 运行刚才写的 `selenium_order.py`

**用到的工具：** `code_run`（python type），用 subprocess 调 PowerShell

**指令（在 code_run 里运行）：**
```python
import subprocess

r = subprocess.run([
    'powershell.exe', '-NoProfile', '-Command',
    '& "C:\\Users\\zhanghuanyu\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" "D:\\work\\Hackthon\\GenericAgent\\temp\\selenium_order.py"'
], capture_output=True, text=True, timeout=30)
print("STDOUT:")
print(r.stdout)
if r.stderr:
    print("STDERR:")
    print(r.stderr[:500])
```

**📛 踩坑记录 — 执行相关：**
> ❌ **坑 10**：用 `subprocess.run(..., timeout=30)` 必须给够时间，如果 SKU 选择 + 跳转总共需要约 8-10 秒，timeout 设 15 秒以上。
>
> ❌ **坑 11**：如果报 `can't open file 'D:\\mnt\\d\\...'` → 路径写错了！Windows 下必须用 `D:\work\Hackthon\...` 格式，不是 `/mnt/d/...`。
>
> ❌ **坑 12（非天猫店铺SKU选择）**：淘宝普通店铺（非天猫）的 SKU 选择器可能用 `new MouseEvent('click')` 才生效，`element.click()` 可能被 React 吞掉。分步操作：
>   - 先点颜色 → 确认出现 `isSelected--_a9zOp7C` 类名 → 再点尺码 → 再点领券购买
>   - 不能用同一个循环同时点两个属性，React 状态来不及更新
>
> ❌ **坑 13（免密支付按钮点击）**：结算页的"免密支付"按钮需要发送完整事件链（`pointerdown→mousedown→pointerup→mouseup→click`）才能触发。单次 `dispatchEvent(new MouseEvent('click'))` 可能无效。
>
> ❌ **坑 14（浏览器选择 — Chrome vs Edge）**：
>   - Edge 对淘宝天猫的兼容性更好（天然 Chromium + 微软在中国的 CDN 优化），登录态保持更稳定
>   - Chrome 在淘宝详情页偶发 React 组件渲染不全（SKU 面板空白、价格不显示）
>   - **推荐：购物类任务默认使用 Edge。** 如果 Edge 不可用再 fallback 到 Chrome
>
> ❌ **坑 15（URL 含 skuId 不等于选中）**：URL 参数出现 `skuId=...` 不代表 UI 上已选中 SKU。必须实际检查 DOM 中是否有 `.isSelected--` / `.selected` / `.active` 类。即使 `web_scan` 显示价格和尺码，也需用 JS 确认 `getComputedStyle` 的边框颜色变化。
>
> ❌ **坑 16（点击按钮后页面无跳转）**：淘宝普通店铺点击"领券购买"后可能弹出 toast 提示而不是跳转。弹出"请选择完整的商品规格"说明 SKU 选中失败 → 清空选择重新选；普通淘宝店购买成功会跳转到 `buy.taobao.com/auction/buy_now.jhtml`（不是天猫的 `buy.tmall.com/order/confirm_order.htm`）。

---

### 步骤 3：解读脚本输出 & 判断成功

**成功信号（全部满足才算下单成功）：**
```
✅ 步骤 1.4 输出: "点击了: 卡其色"
✅ 步骤 1.5 输出: "点击了: XL"
✅ 步骤 1.6 显示: selected 包含 ["卡其色", "XL"]
✅ 步骤 1.7 输出: "已点击领券购买按钮"
✅ 最终URL: https://buy.tmall.com/order/confirm_order.htm?x-itemid=...
✅ 最终标题: "确认订单"
```

**失败排查表：**

| 现象 | 原因 | 解决 |
|:---|:---|:---|
| "未找到卡其色" | 文本不匹配（可能是"卡其【100%纯棉】"） | 改用 `startsWith('卡其')` 而非全等 |
| "未找到 XL" | XL 被包裹在容器里，`offsetParent` 为 null | 去掉 `offsetParent` 检查，或遍历更深层元素 |
| 点完卡其色后 selected 仍为空 | React 状态未更新 | 增加 `time.sleep(2)` 再检查 |
| "未找到领券购买按钮" | 页面还没加载完 | 先 `time.sleep(2)` 等页面渲染 |
| URL 没变，仍停留在详情页 | 按钮点击没生效 | 检查是否 disabled（`element.disabled`），尝试找父级元素点击 |
| 弹出"请先选择完整的商品规格" | SKU 没选全 | 确认颜色+尺码都已选，selected class 已出现 |
| 颜色尺码都点了仍然弹出"请选择商品规格" | 同一循环内同时点两个属性，React 没来得及更新 | 必须分步：先点颜色 → 等 DOM 出现 isSelected 类 → 再点尺码 |
| URL 已有 skuId 但结算页跳不过去 | 浏览器非 Chrome（如 Edge）对淘宝 React 组件的兼容差异 | 推荐用 Edge 购物 |
| 点击"领券购买"无任何弹窗/跳转 | 淘宝普通店铺（非天猫）需用 `new MouseEvent('click')`，`element.click()` 被吞 | 用 `dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}))` |
| 结算页点"免密支付"无反应 | 按钮需要完整鼠标事件链 | 发送 `pointerdown→mousedown→pointerup→mouseup→click` 完整序列 |
| 页面跳转到 buy.taobao.com 的结算页后无法勾选地址/支付 | 淘宝普通店铺的结算页与天猫结构不同 | 确认页默认地址可能已选中，只需点击"免密支付¥XX"按钮 |

---

### 步骤 4：通知用户手动完成支付

**做什么：** 页面已到确认订单页，Agent 禁止碰支付

**用到的工具：** `ask_user`

**指令：**
```python
ask_user(
    question="已到达天猫确认订单页，请你在浏览器中完成提交订单和支付",
    candidates=["✅ 我自己去下单", "📋 帮我看看订单详情", "❌ 不买了"]
)
```

---

### 完整成功日志（参考：本次真实会话的输出）
```
当前页面: 真维斯纯棉短袖t恤男夏季宽松休闲上衣简约纯色基础款重磅打底衫
当前URL: https://detail.tmall.com/item.htm?id=892828389189&sku_properties=20509%3A28317
SKU 选中状态: {"selected":[], ...}

步骤 1.4: 选择卡其色...
点击了: 卡其【100%纯棉】

步骤 1.5: 选择 XL...
点击了: XL

选中后状态: {"selected":["卡其色","XL"],"url":"...&skuId=5741869457626"}

步骤 1.7: 点击领券购买...
已点击领券购买按钮

最终URL: https://buy.tmall.com/order/confirm_order.htm?x-itemid=892828389189
最终标题: 确认订单

✅ 下单成功！已到达确认订单页
```

## 错误处理 & 降级

| 场景 | 第1次处理 | 第2次处理 | 第3次处理 |
|:---|:---|:---|:---|
| 搜索结果为空 | 用同义词/放宽关键词 | 减少筛选条件，扩大价格区间 | 建议换平台搜索，问用户是否继续 |
| 页面加载不完整（反爬/验证码） | `web_scan` 等待 2s 重试 | 换搜索词或换排序方式 | 降级: 告诉用户"当前平台搜索受限，建议手动打开页面我来读取" |
| 评价区无法抓取 | 检查是否需要登录/滚动加载 | 换个评价 tab（如最新/有图） | 标记"评价信息不完整"，不隐藏此缺陷 |
| 价格抓取异常（非数字/空） | 多定位几个价格元素尝试 | 从详情页参数表提取 | 标记"价格未确认"，让用户核实 |
| 优惠券/满减检测失败 | 检查购物车页面是否加载 | 手动查看店铺首页活动 | 告知用户"可能漏掉优惠，建议自行确认" |
| 用户中途改变需求 | 回到 CLARIFY 状态，保留已收集的部分信息 | — | — |
| 候选集死锁（连续 2 轮不收敛） | 主动问用户缺少什么决策维度 | 按"销量×评分/价格"综合排序，Top 2 强制推进 | — |
| ask_user 等待超时（用户离开） | 保存当前进度到 checkpoint | — | — |
| 极低价/异常价（<市场价 50%） | ⛔ 立即警告用户可能是假货/骗局 | 检查店铺资质（开店时间/评分/销量） | 用户坚持 → 不阻止但再次提醒 |
| 下单时库存不足 | 建议备选规格/店铺 | 回到 PRESENT 展示剩余候选 | — |
| 支付环节 | ⛔ Agent 禁止参与，用户自行完成 | — | — |

---

## 完整购物示例

### 示例 1：明确购买意图

```
用户: "帮我买个机械键盘，500以内，要红轴的"

TURN 0 [CLARIFY]
  记忆注入: L2 内无键盘相关偏好
  意图分类: INTENT_BUY
  约束提取: 品类=机械键盘 | 预算=500 | 开关类型=红轴 | 品牌=未指定 | 用途=未指定
  约束完整 → 进入 SEARCH

TURN 1 [SEARCH + FILTER + PRESENT]
  打开京东搜索"机械键盘 红轴" → 价格筛选0-500 → 按销量排序
  收集 20 个候选 → 去掉非红轴/明显山寨 → 剩 8 个
  呈现 Top 4:
  
  | # | 品牌型号 | 价格 | 销量 | 评分 | 亮点 |
  |---|---------|------|------|------|------|
  | 1 | 狼蛛 F87 | ¥199 | 10万+ | 4.8 | 性价比之王，热插拔 |
  | 2 | 达尔优 A87 | ¥329 | 5万+ | 4.7 | PBT键帽，RGB背光 |
  | 3 | 黑峡谷 X5 | ¥399 | 3万+ | 4.8 | 凯华BOX轴，无线三模 |
  | 4 | IKBC C87 | ¥459 | 2万+ | 4.6 | Cherry原厂轴，简约 |

  ask_user: "你更看重哪个方面？(a)性价比 (b)无线 (c)RGB灯光 (d)87键紧凑 (e)品牌口碑"

TURN 2 [REFINE → SEARCH]
  用户: "我要无线的"
  约束更新: +连接方式=无线/三模
  重新筛选 → 只剩 2/3/...
  候选 3 个，进入 DEEP_EVAL

TURN 3 [DEEP_EVAL + DECIDE]
  逐个查看评价: 黑峡谷X5差评集中在"蓝牙偶尔断连"，达尔优评价稳定
  推荐: 达尔优 A87 无线版 ¥349
  理由: 预算内、评价稳定、三模连接、PBT键帽耐用
  ask_user: "推荐达尔优 A87，要下单吗？"

TURN 4 [EXECUTE]
  用户: "买"
  确认规格(红轴/黑色) → 确认地址(从L2读取) → 检查优惠券
  → 加购 → 进入结算页 → 用户自己支付
  → 记录订单到 checkpoint + 更新 L2: "键盘预算: 300-500"
```

### 示例 2：模糊探索意图

```
用户: "想买件外套，但不太确定什么款式"

TURN 0 [CLARIFY]
  意图分类: INTENT_EXPLORE
  约束: 品类=外套 | 季节/风格/预算/品牌=全部未知
  L2 注入: 风格偏好=简约, 颜色偏好=卡其色/白色系
  ask_user: "现在是什么季节穿？预算大概多少？"

用户: "秋天穿，500以内吧"

TURN 1 [SEARCH + PRESENT] — 探索轮
  搜索"秋装外套 男" → 按销量排序 → 提取款式分类
  呈现:
  "目前秋季外套主要有这几类，你看看哪个方向感兴趣:
   (a) 牛仔夹克 — 经典百搭，¥150-400
   (b) 工装夹克 — 军事风，多口袋，¥200-500
   (c) 棒球服 — 运动休闲，¥100-350
   (d) 针织开衫 — 文艺柔和，¥150-400
   (e) 风衣/薄大衣 — 气质路线，¥300-500"

用户: "工装夹克看起来不错"

TURN 2 [SEARCH + PRESENT] — 锁定方向
  搜索"工装夹克 男 简约" → 筛选 0-500 → 销量排序
  注入颜色偏好(卡其色优先)
  呈现 4 个候选...（继续标准流程）
```

---

## 品类特定维度参考

不同品类在 PRESENT 阶段需要展示不同的关键属性：

| 品类 | 必展示维度 | 可选维度 |
|:---|:---|:---|
| 服装/鞋 | 尺码、面料、风格 | 季节、版型、品牌 |
| 电子产品 | 核心参数、接口、续航 | 重量、配件、保修 |
| 食品 | 规格/重量、保质期、配料 | 产地、品牌、储存方式 |
| 家电 | 功率/容量、能效等级、尺寸 | 噪音、智能功能、安装 |
| 家居 | 材质、尺寸、风格 | 安装难度、保养要求 |
| 美妆 | 规格、适用肤质、功效 | 成分、保质期 |
| 图书 | 作者、出版社、版本 | 页数、装帧、评分 |

在 PRESENT 时自动匹配对应维度，用表格呈现。
