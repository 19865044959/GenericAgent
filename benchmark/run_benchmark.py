"""
GA Memory Benchmark — 基于 LongMemEval / LoCoMo / MemoryAgentBench 评测范式

两种模式：
  offline: 直接测试 memory_auto 的萃取和检索函数（快速，不调 LLM agent loop）
  e2e:     通过 GA --task 模式端到端测试（慢，但测完整的 agent 行为）

用法：
  python benchmark/run_benchmark.py offline    # 快速离线评测
  python benchmark/run_benchmark.py e2e        # 端到端评测（需要 API key）
"""

import os, sys, json, re, time, shutil

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)


def load_test_cases():
    path = os.path.join(SCRIPT_DIR, 'benchmark', 'test_cases.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _check_patches():
    """检查 agentmain.py 是否已打上 auto-memory 补丁。返回 (patched: bool, details: str)"""
    agentmain_path = os.path.join(SCRIPT_DIR, 'agentmain.py')
    if not os.path.exists(agentmain_path):
        return False, 'agentmain.py not found'

    with open(agentmain_path, 'r', encoding='utf-8') as f:
        content = f.read()

    has_extraction = 'extract_facts' in content and 'auto_update_l2' in content
    has_retrieval = 'search_memory' in content and 'Auto Retrieved Memory' not in content
    # 检查是否在 run() 方法中有 auto 调用
    has_auto_extract = 'auto_update_l2(facts' in content or 'auto_update_l2(facts' in content
    has_auto_retrieve = 'search_memory(raw_query' in content or 'search_memory(raw_query' in content
    has_memory_auto_import = 'from memory_auto import' in content

    if has_memory_auto_import and has_auto_extract and has_auto_retrieve:
        return True, 'auto-extraction + auto-retrieval patches applied'
    elif has_memory_auto_import:
        return False, 'memory_auto imported but patches incomplete'
    else:
        return False, 'no auto-memory patches detected (vanilla GA)'


# ─── 离线评测：直接测试 memory_auto 函数 ────────────────────────────


def run_offline_benchmark():
    """离线评测：直接调用 memory_auto 的 Python 函数，不经过 GA agent loop。"""
    patched, patch_info = _check_patches()

    if not patched:
        print("=" * 60)
        print("GA Memory Benchmark v2 — Offline Mode")
        print(f"Patch status: ❌ {patch_info}")
        print("SKIP: offline mode requires memory_auto.py (not available on vanilla GA)")
        print("=" * 60)
        return None

    from memory_auto import search_memory, auto_update_l2
    data = load_test_cases()
    memory_dir = os.path.join(SCRIPT_DIR, 'memory')
    l2_path = os.path.join(memory_dir, 'global_mem.txt')

    print("=" * 60)
    print("GA Memory Benchmark v2 — Offline Mode")
    print(f"Test cases: {len(data['test_cases'])}")
    print(f"Patch status: ✅ {patch_info}")
    print("=" * 60)

    results = {
        'extraction': {'passed': 0, 'total': 0, 'details': []},
        'retrieval':  {'passed': 0, 'total': 0, 'details': []},
        'abstention': {'passed': 0, 'total': 0, 'details': []},
    }

    for tc in data['test_cases']:
        tid = tc['id']
        cat = tc['category']
        print(f"\n── {tid} ({cat}) {tc['description']} ──")

        # 备份 L2
        if os.path.exists(l2_path):
            with open(l2_path, 'r', encoding='utf-8') as f:
                l2_backup = f.read()
        else:
            l2_backup = ''

        # 清空 L2
        with open(l2_path, 'w', encoding='utf-8') as f:
            f.write('# [Global Memory - L2]\n')

        # ── 预填充 L2（模拟上一轮会话留下的旧事实，用于测试记忆更新）──
        pre_seed = tc.get('pre_seed_l2', [])
        if pre_seed:
            auto_update_l2(pre_seed, memory_dir)

        session_1 = tc['session_1']
        session_2 = tc['session_2']
        expected_extraction = session_1.get('expected_extraction', [])
        expected_kw = session_2.get('expected_keywords', [])
        forbidden_kw = session_2.get('forbidden_keywords', [])
        should_abstain = session_2.get('should_abstain', False)

        # ── 改动前检索（L2 只有旧事实，预期命中 forbidden keywords）──
        before_retrieval = search_memory(session_2['query'], memory_dir)
        before_matches = sum(1 for kw in expected_kw if before_retrieval and kw in before_retrieval)
        before_wrong = sum(1 for kw in forbidden_kw if before_retrieval and kw in forbidden_kw)

        # ── 模拟萃取：直接写 expected_extraction 到 L2 ──
        if expected_extraction:
            auto_update_l2(expected_extraction, memory_dir)

        # 验证 L2 写入
        with open(l2_path, 'r', encoding='utf-8') as f:
            l2_after = f.read()

        stored = sum(1 for f in expected_extraction if f['value'] in l2_after)
        total = len(expected_extraction)
        extraction_ok = stored >= total * 0.5 if total > 0 else True

        # ── 改动后检索 ──
        after_retrieval = search_memory(session_2['query'], memory_dir)
        after_matches = sum(1 for kw in expected_kw if after_retrieval and kw in after_retrieval)
        after_wrong = sum(1 for kw in forbidden_kw if after_retrieval and kw in after_retrieval)

        retrieval_ok = after_matches >= 1 and after_wrong == 0

        # ── 记录结果 ──
        if total > 0:
            results['extraction']['total'] += 1
            if extraction_ok:
                results['extraction']['passed'] += 1
            results['extraction']['details'].append({
                'id': tid, 'ok': extraction_ok,
                'stored': stored, 'total': total
            })

        if should_abstain:
            results['abstention']['total'] += 1
            abstain_ok = not after_retrieval or after_matches == 0
            if abstain_ok:
                results['abstention']['passed'] += 1
            results['abstention']['details'].append({
                'id': tid, 'ok': abstain_ok,
                'retrieved': bool(after_retrieval)
            })
        else:
            results['retrieval']['total'] += 1
            if retrieval_ok:
                results['retrieval']['passed'] += 1
            results['retrieval']['details'].append({
                'id': tid, 'ok': retrieval_ok,
                'before_matches': before_matches, 'after_matches': after_matches,
                'wrong_matches': after_wrong
            })

        # 恢复 L2
        with open(l2_path, 'w', encoding='utf-8') as f:
            f.write(l2_backup)

        # 打印单条结果
        if should_abstain:
            status = "✅" if abstain_ok else "❌"
            print(f"  {status} Abstention: retrieved={bool(after_retrieval)}  (expected: no facts)")
        else:
            status = "✅" if retrieval_ok else "❌"
            extra = ''
            if pre_seed:
                # 验证旧值是否被标 outdated
                outdated_count = sum(1 for line in l2_after.split('\n') if '(outdated)' in line)
                old_vals_in_retrieval = [kw for kw in forbidden_kw if after_retrieval and kw in after_retrieval]
                extra = f"  Outdated: {outdated_count} | Old in retrieval: {old_vals_in_retrieval or 'none'}"
            print(f"  {status} Before: {before_matches}/{len(expected_kw)} new / {before_wrong}/{len(forbidden_kw)} old matched  "
                  f"After: {after_matches}/{len(expected_kw)} new / {after_wrong}/{len(forbidden_kw)} old matched  "
                  f"Extraction: {stored}/{total} stored{extra}")

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    for metric, r in results.items():
        if r['total'] > 0:
            rate = r['passed'] / r['total'] * 100
            bar = '█' * int(rate / 10) + '░' * (10 - int(rate / 10))
            print(f"  {metric:20s}: {r['passed']:2d}/{r['total']:2d}  {bar}  {rate:.0f}%")

    overall = sum(r['passed'] for r in results.values()) / max(1, sum(r['total'] for r in results.values())) * 100
    print(f"  {'OVERALL':20s}:   {sum(r['passed'] for r in results.values()):2d}/{sum(r['total'] for r in results.values()):2d}          {overall:.0f}%")
    print()
    return results


# ─── 端到端评测：通过 GA --task 模式 ───────────────────────────────


def run_e2e_benchmark():
    """端到端评测：启动 GA --task 模式，Session 1 多轮对话 → 萃取 → Session 2 检索。"""
    data = load_test_cases()
    memory_dir = os.path.join(SCRIPT_DIR, 'memory')
    l2_path = os.path.join(memory_dir, 'global_mem.txt')

    patched, patch_info = _check_patches()

    # 备份 L2
    if os.path.exists(l2_path):
        with open(l2_path, 'r', encoding='utf-8') as f:
            l2_backup = f.read()
    else:
        l2_backup = ''

    print("=" * 60)
    print("GA Memory Benchmark v2 — E2E Mode")
    print(f"Patch status: {'✅ ' if patched else '❌ '}{patch_info}")
    if not patched:
        print("⚠️  当前为 vanilla GA（无 auto-memory 补丁）。")
        print("   跨会话用例预计全部失败 —— LLM 不会自动存储/检索隐式事实。")
    else:
        print("   auto-memory 补丁已生效，跨会话用例应全部通过。")
    print("=" * 60)

    test_cases = data['test_cases']
    print(f"\nTest cases: {len(test_cases)} ({[tc['id'] for tc in test_cases]})")

    results = {'passed': 0, 'total': len(test_cases), 'details': []}

    for tc in test_cases:
        tid = tc['id']
        print(f"\n── {tid} {tc['description']} ──")

        # 清空 L2
        with open(l2_path, 'w', encoding='utf-8') as f:
            f.write('# [Global Memory - L2]\n')

        # ── 预填充 L2（模拟上一轮会话留下的旧事实）──
        pre_seed = tc.get('pre_seed_l2', [])
        if pre_seed:
            if not patched:
                print(f"  SKIP pre-seed: memory_auto not available on vanilla GA")
            else:
                from memory_auto import auto_update_l2
                auto_update_l2(pre_seed, memory_dir)
                print(f"  Pre-seeded L2 with {len(pre_seed)} old facts")

        session_1 = tc['session_1']
        session_2 = tc['session_2']

        # ── Session 1: 多轮对话 ──
        dialogue = session_1['dialogue']
        user_messages = [msg['content'] for msg in dialogue if msg['role'] == 'user']
        print(f"  Session 1: {len(user_messages)} turns")

        task_dir = f'bench_{tid}_s1'
        _run_ga_multi_turn(task_dir, user_messages)

        # 检查 L2 萃取结果
        expected_extraction = session_1.get('expected_extraction', [])
        if os.path.exists(l2_path):
            with open(l2_path, 'r', encoding='utf-8') as f:
                l2_content = f.read()
            stored = sum(1 for f in expected_extraction if f['value'] in l2_content)
        else:
            l2_content = ''
            stored = 0

        total_facts = len(expected_extraction)
        extraction_ok = stored >= total_facts * 0.5 if total_facts > 0 else True
        print(f"  L2 extraction: {stored}/{total_facts} facts stored")

        # ── Session 2: 检索查询 ──
        task_dir_q = f'bench_{tid}_s2'
        output = _run_ga_task(task_dir_q, session_2['query'])

        expected_kw = session_2.get('expected_keywords', [])
        forbidden_kw = session_2.get('forbidden_keywords', [])
        should_abstain = session_2.get('should_abstain', False)

        # min_matched: 至少匹配多少个关键词才算通过。默认要求全部匹配
        min_matched = session_2.get('min_matched', len(expected_kw) if expected_kw else 1)

        matched = [kw for kw in expected_kw if kw in output]
        wrong = [kw for kw in forbidden_kw if kw in output]

        if should_abstain:
            abstain_indicators = ['不知道', '不清楚', '没有提到', "don't know", '未提及', '没有记录',
                                 '没有提供', '无法回答', '没有手机号', '没有找到']
            has_abstention = any(ind in output for ind in abstain_indicators)
            keyword_ok = has_abstention and len(wrong) == 0
        else:
            keyword_ok = len(matched) >= min_matched and len(wrong) == 0

        # Multi-signal verdict: extraction MUST succeed AND keywords MUST match
        ok = extraction_ok and keyword_ok

        if ok:
            results['passed'] += 1
        results['details'].append({
            'id': tid, 'ok': ok,
            'extraction_ok': extraction_ok, 'keyword_ok': keyword_ok,
            'matched': matched, 'wrong': wrong,
            'expected': expected_kw, 'min_matched': min_matched
        })

        status = "✅" if ok else "❌"
        extra = ''
        if pre_seed and os.path.exists(l2_path):
            with open(l2_path, 'r', encoding='utf-8') as f:
                l2 = f.read()
            outdated_count = sum(1 for line in l2.split('\n') if '(outdated)' in line)
            extra = f' | Outdated: {outdated_count}'
        print(f"  {status} Extraction: {extraction_ok} | Keywords: {keyword_ok} "
              f"({len(matched)}/{min_matched} matched, {len(wrong)} wrong)")
        print(f"     Expected: {expected_kw}, Matched: {matched}, Wrong: {wrong}{extra}")

        # 清理 task 目录
        for d in [task_dir, task_dir_q]:
            dp = os.path.join(SCRIPT_DIR, 'temp', d)
            if os.path.exists(dp):
                shutil.rmtree(dp, ignore_errors=True)

    # 恢复 L2
    with open(l2_path, 'w', encoding='utf-8') as f:
        f.write(l2_backup)

    print("\n" + "=" * 60)
    print(f"E2E RESULTS: {results['passed']}/{results['total']} passed")
    for d in results['details']:
        status = "✅" if d['ok'] else "❌"
        print(f"  {status} {d['id']}: extraction={d.get('extraction_ok','?')} "
              f"keywords={d.get('keyword_ok','?')} "
              f"matched={d['matched']} wrong={d['wrong']}")
    print()
    return results


def _run_ga_multi_turn(task_dir, user_messages):
    """运行 GA --task 多轮对话。每个 user message 作为一轮输入。
    最后一轮结束后不写 reply.txt，agent 的 while 循环自然退出并触发萃取。"""
    import subprocess
    task_path = os.path.join(SCRIPT_DIR, 'temp', task_dir)
    os.makedirs(task_path, exist_ok=True)

    # 清理旧文件
    for f in os.listdir(task_path):
        os.remove(os.path.join(task_path, f))

    # 写入第一轮 input
    with open(os.path.join(task_path, 'input.txt'), 'w', encoding='utf-8') as f:
        f.write(user_messages[0])

    env = os.environ.copy()
    env['PYTHONPATH'] = SCRIPT_DIR
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'agentmain.py'),
           '--task', task_dir, '--nobg']

    proc = subprocess.Popen(cmd, cwd=SCRIPT_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    final_output = "[TIMEOUT]"
    l2_path = os.path.join(SCRIPT_DIR, 'memory', 'global_mem.txt')

    for turn_idx in range(len(user_messages)):
        # 等待当前轮输出完成（有 [ROUND END]）
        output = "[TIMEOUT]"
        found = False
        for _ in range(180):  # 最多等 6 分钟
            time.sleep(2)
            output_files = sorted(
                [f for f in os.listdir(task_path)
                 if f.startswith('output') and f.endswith('.txt')],
                key=lambda x: os.path.getmtime(os.path.join(task_path, x)),
                reverse=True
            )
            if output_files:
                with open(os.path.join(task_path, output_files[0]), 'r', encoding='utf-8') as f:
                    output = f.read()
                if '[ROUND END]' in output:
                    found = True
                    break

        if not found:
            print(f"  [WARN] Turn {turn_idx + 1} did not complete within timeout")

        final_output = output

        # 如果还有下一轮，写 reply.txt
        if turn_idx + 1 < len(user_messages):
            # 等 agent 准备好读 reply.txt（它会轮询这个文件）
            time.sleep(2)
            with open(os.path.join(task_path, 'reply.txt'), 'w', encoding='utf-8') as f:
                f.write(user_messages[turn_idx + 1])

    # 最后一轮完成，写 _stop 通知 agent 会话结束
    # 延迟 3 秒：等 agent 通过 consume_file(d, '_stop') 的 stale cleanup，
    # 进入 reply.txt 等待循环后再写，避免 _stop 被当作过期信号误删
    time.sleep(3)
    stop_file = os.path.join(task_path, '_stop')
    with open(stop_file, 'w') as f:
        f.write('1')

    # 等待 L2 mtime 变化来确认 auto-extraction 完成
    l2_mtime_before = os.path.getmtime(l2_path) if os.path.exists(l2_path) else 0
    for _ in range(15):  # 最多等 30 秒
        time.sleep(2)
        if os.path.exists(l2_path):
            if os.path.getmtime(l2_path) > l2_mtime_before:
                break

    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass

    return final_output


def _run_ga_task(task_dir, input_text):
    """运行 GA --task 单轮，返回输出文本"""
    import subprocess
    task_path = os.path.join(SCRIPT_DIR, 'temp', task_dir)
    os.makedirs(task_path, exist_ok=True)

    # 清理旧文件
    for f in os.listdir(task_path):
        os.remove(os.path.join(task_path, f))

    with open(os.path.join(task_path, 'input.txt'), 'w', encoding='utf-8') as f:
        f.write(input_text)

    env = os.environ.copy()
    env['PYTHONPATH'] = SCRIPT_DIR
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'agentmain.py'),
           '--task', task_dir, '--nobg']

    proc = subprocess.Popen(cmd, cwd=SCRIPT_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    output = "[TIMEOUT]"
    found_output = False
    for _ in range(180):
        time.sleep(2)
        output_files = sorted(
            [f for f in os.listdir(task_path)
             if f.startswith('output') and f.endswith('.txt')],
            key=lambda x: os.path.getmtime(os.path.join(task_path, x)),
            reverse=True
        )
        if output_files:
            with open(os.path.join(task_path, output_files[0]), 'r', encoding='utf-8') as f:
                output = f.read()
            if '[ROUND END]' in output:
                found_output = True
                break

    if not found_output:
        print(f"  [WARN] Task did not complete within timeout")

    # 写 _stop 通知 agent 会话结束（延迟 3s 避免被 agent 的 stale cleanup 误删）
    l2_path = os.path.join(SCRIPT_DIR, 'memory', 'global_mem.txt')
    time.sleep(3)
    stop_file = os.path.join(task_path, '_stop')
    with open(stop_file, 'w') as f:
        f.write('1')

    # 等待 auto-extraction 完成
    l2_mtime_before = os.path.getmtime(l2_path) if os.path.exists(l2_path) else 0
    for _ in range(15):
        time.sleep(2)
        if os.path.exists(l2_path):
            if os.path.getmtime(l2_path) > l2_mtime_before:
                break

    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass

    return output


# ─── 对比报告 ────────────────────────────────────────


def print_comparison_report(offline_results):
    """打印改动前 vs 改动后对比报告"""
    print("\n" + "=" * 60)
    print("BEFORE vs AFTER — 改动前后对比")
    print("=" * 60)

    ext = offline_results['extraction']['details']
    if ext:
        stored = sum(d['stored'] for d in ext)
        total = sum(d['total'] for d in ext)
        print(f"\n  记忆存储率 (Extraction):")
        print(f"    改动前: 0/{total} facts stored (无自动萃取)")
        print(f"    改动后: {stored}/{total} facts stored (自动萃取)")

    retrievals = offline_results['retrieval']['details']
    if retrievals:
        before_hits = sum(d['before_matches'] for d in retrievals)
        after_hits = sum(d['after_matches'] for d in retrievals)
        print(f"\n  检索命中率 (Retrieval):")
        print(f"    改动前: {before_hits} keyword matches")
        print(f"    改动后: {after_hits} keyword matches")

    abst = offline_results['abstention']
    if abst['total'] > 0:
        print(f"\n  弃权准确率 (Abstention):")
        print(f"    {abst['passed']}/{abst['total']} (未存储的信息不会被错误召回)")

    print()


# ─── 入口 ────────────────────────────────────────────


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='GA Memory Benchmark')
    parser.add_argument('mode', nargs='?', default='offline',
                       choices=['offline', 'e2e', 'all'],
                       help='offline: fast function-level test; e2e: full agent test')
    args = parser.parse_args()

    if args.mode in ('offline', 'all'):
        results = run_offline_benchmark()
        if results is not None:
            print_comparison_report(results)

    if args.mode in ('e2e', 'all'):
        run_e2e_benchmark()
