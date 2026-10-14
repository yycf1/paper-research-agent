/* Offline browser workflow checks. Every API request uses original synthetic fixtures. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const output = path.resolve(process.env.WEB_QA_OUTPUT || 'data/web-qa');
fs.mkdirSync(output, { recursive: true });
const base = process.env.WEB_QA_URL || 'http://127.0.0.1:18809';
const titles = ['Evidence-aware Research Question Answering', 'Graph Learning with Explicit Evaluation Protocols', 'Vision Models: A Local Reading Example'];
function paper(n) { return { source_id: `local:demo-${n}`, source: 'local', title: titles[n - 1] || 'Newly Discovered Research Example', authors: ['原创界面示例'], year: 2024 + n % 2, doi: null, citations: null, has_pdf: true, landing_page: null, status: n === 3 ? 'parsed' : 'indexed', error: null, added_at: '2026-10-14T10:00:00', tag: n === 3 ? '视觉研究' : '检索与智能体', abstract: '原创离线界面示例，仅用于验证交互。' }; }
function fields(p) {
  const fact = (field, value) => ({ field, raw_value: value, normalized_value: value, citation_status: 'valid', support_status: 'unverified', claim_sha256: `fixture-${p.source_id}-${field}`, issues: [], review: null, evidence: [{ paper_id: p.source_id, chunk_id: `${p.source_id}::0`, section: '4 Experiments', page: 3, quote: 'Synthetic-A uses a fixed test split; Recall@10 is reported as a ratio.' }] });
  return { schema_version: 1, paper_id: p.source_id, source_version: 'original-offline-fixture', fields: { research_goal: [fact('research_goal', '研究带证据的文献问答')], datasets: [fact('datasets', { name: 'Synthetic-A', split: 'fixed test split' })], metrics: [fact('metrics', { name: 'Recall@10', value: '0.5', task: 'retrieval', dataset: 'Synthetic-A', split: 'fixed test split', definition: 'recall at 10', unit: 'ratio', evaluation_protocol: 'full candidate set', model: 'Demo' })] }, omitted: {}, unmatched_reviews: 0, load_issues: [], numeric_comparison: { ranking_allowed: false, reason: '条件仍需核对' } };
}
function detail(p) { return { ...p, experiment: { research_goal: '研究带证据的文献问答', method: { name: 'Demo' }, datasets: [], metrics: [], generated_at: '2026-10-14T10:00:00' }, report: '## 一句话总结\n这是一份原创离线阅读示例。\n\n## 实验设计\nSynthetic-A 固定测试划分，报告 Recall@10；尚需核对原文依据。', repro_plan: null, full_text: '## Method\nOriginal synthetic text for browser verification.\n\n## Experiments\nSynthetic-A uses a fixed test split.', index_state: { status: p.status === 'indexed' ? 'ready' : 'unindexed', chunk_count: p.status === 'indexed' ? 2 : 0, can_ask: p.status === 'indexed' }, knowledge_current: true }; }
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_BROWSER_PATH ? { executablePath: process.env.PLAYWRIGHT_BROWSER_PATH } : {}) });
  const checks = [], errors = [], requests = [];
  try {
    for (const [profile, width, height] of [['desktop', 1440, 1000], ['desktop-1280', 1280, 900]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      const page = await context.newPage();
      await page.addInitScript(() => { Object.defineProperty(navigator, 'clipboard', { value: { writeText: async text => { window.__copiedAnswer = text; } } }); });
      let papers = [paper(1), paper(2), paper(3)];
      let pending = true;
      page.on('pageerror', error => errors.push({ profile, error: String(error) }));
      await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin !== new URL(base).origin) { errors.push({ profile, error: 'External request blocked: ' + url.origin }); return route.abort(); }
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const name = decodeURIComponent(url.pathname.slice(4));
        const method = request.method();
        const body = method === 'POST' || method === 'PUT' ? request.postDataJSON() : null;
        requests.push({ profile, path: name, method, body });
        const respond = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
        const task = { id: 'waiting-demo', task: '确认后执行一个示例操作', status: pending ? 'waiting_confirm' : 'done', steps: [], step_count: 0, pending_confirm: pending ? { count: 1, titles: ['示例操作：仅测试确认界面'] } : null, answer: pending ? '' : '已拒绝示例操作。', stopped_reason: 'final', usage: { prompt: 0, completion: 0 }, error: '', allowed_paper_ids: ['local:demo-1'], live: true, ts: 1791964800 };
        if (name === '/status') return respond({ version: 'demo', total: papers.length, counts: { indexed: 2, parsed: 1 }, vector_chunks: 6 });
        if (name === '/settings') return respond({ base_url: 'https://example.invalid/v1', model: 'offline-fixture', api_key_masked: '', has_key: true, theme: 'light' });
        if (name === '/tags') return respond({ tags: [{ name: '检索与智能体', count: 2 }, { name: '视觉研究', count: 1 }], sources: { local: papers.length }, untagged: 0 });
        if (name === '/papers' && method === 'GET') {
          const query = (url.searchParams.get('q') || '').toLowerCase();
          const selected = papers.filter(p => p.title.toLowerCase().includes(query) && (!url.searchParams.get('status') || p.status === url.searchParams.get('status')) && (!url.searchParams.get('tag') || p.tag === url.searchParams.get('tag')));
          return respond({ papers: selected, total: selected.length });
        }
        if (name === '/sessions') return respond({ sessions: [] });
        if (name === '/agent/tasks' && method === 'GET') return respond({ tasks: [task] });
        if (name === '/agent/tasks/waiting-demo/confirm') { assert.equal(body.approve, false); pending = false; return respond({ ok: true }); }
        if (name === '/agent/tasks/waiting-demo') return respond(task);
        if (name === '/jobs') return respond({ jobs: [{ id: 'process-demo', kind: 'process_paper', status: 'done', result: { source_id: 'local:demo-3', final_status: 'parsed', stages: [{ stage: 'report', label: '生成精读报告', status: 'failed', detail: '离线失败场景示例' }] }, error: '' }] });
        if (name === '/search') return respond({ candidates: [{ ...paper(4), duplicate_of: null }], total_hits: 1, notes: [], source_hits: { openalex: 1 }, topics: [body.query], optimized: false });
        if (name === '/papers/ingest') { papers.push({ ...paper(4), tag: body.tag }); return respond({ ingested: 1, duplicates: 0, papers: [paper(4)] }); }
        if (name === '/ask') {
          assert.deepEqual(body.paper_ids, ['local:demo-1', 'local:demo-2']);
          return respond({ answer: '使用 Synthetic-A 的固定测试划分 [1]。', answer_status: 'generated', citation_check: { status: 'passed', semantic_support: 'not_automatically_judged' }, retrieval_notes: [], sources: [{ n: [1], paper_id: 'local:demo-1', title: titles[0], section: '4 Experiments', page: 3, chunks: [{ n: 1, chunk_id: 'local:demo-1::0', page: 3, text: 'Synthetic-A uses a fixed test split.' }] }] });
        }
        if (name === '/compare') {
          assert.deepEqual(body.paper_ids, ['local:demo-1', 'local:demo-2']);
          return respond({ papers: papers.slice(0, 2).map(p => ({ paper_id: p.source_id, title: p.title, field_evidence: fields(p) })), comparability: { pairs: [{ paper_ids: ['local:demo-1', 'local:demo-2'], metrics: [{ left_metric: 'Recall@10', right_metric: 'Recall@20', missing_conditions: ['评估协议'], different_conditions: ['指标名称/参数'] }] }], ranking_allowed: false } });
        }
        if (name.startsWith('/papers/')) {
          const sid = name.split('/')[2], p = papers.find(p => p.source_id === sid);
          if (name.endsWith('/plan')) return respond({ detail: '模拟模型服务暂不可用' }, 503);
          if (!p) return respond({ detail: 'Not found' }, 404);
          if (name.endsWith('/facts')) return respond(fields(p));
          if (name.endsWith('/repro')) return respond({ source_id: sid, files: [], runs: [] });
          if (method === 'GET') return respond(detail(p));
        }
        errors.push({ profile, error: `Unhandled API ${method} ${name}` });
        return respond({ detail: 'Unmocked API is forbidden in offline verification' }, 500);
      });
      const nav = name => page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name, exact: true });
      const noOverflow = async view => { const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth); checks.push({ profile, view, overflow }); assert.equal(overflow, false, `${profile} ${view} overflow`); };
      await page.goto(base);
      await page.getByRole('button', { name: titles[0], exact: true }).waitFor();
      await page.getByRole('button', { name: /新建分组/ }).click();
      const groupInput = page.getByPlaceholder('分组名');
      await groupInput.fill('研究'); await groupInput.press('Space');
      assert.equal(await groupInput.inputValue(), '研究 ');
      await groupInput.press('Escape');
      checks.push({ profile, view: 'keyboard-groups', textEntryPreserved: true });
      await page.screenshot({ path: path.join(output, `library-${profile}.png`), fullPage: true });
      await noOverflow('library');
      await page.locator('.library-view .lib-q').fill('unmatched-fixture');
      await page.getByText('没有符合筛选条件的论文', { exact: true }).waitFor();
      await page.locator('.library-view .lib-q').fill('');
      await page.getByRole('button', { name: titles[0], exact: true }).waitFor();
      await page.getByRole('checkbox', { name: `选择 ${titles[0]}`, exact: true }).check();
      await page.getByRole('checkbox', { name: `选择 ${titles[1]}`, exact: true }).check();
      await page.getByRole('button', { name: '比较所选论文', exact: true }).click();
      await page.getByRole('textbox', { name: '研究问题' }).fill('比较数据划分和指标参数');
      await page.getByRole('button', { name: '比较所选论文', exact: true }).click();
      await page.getByRole('heading', { name: '论文比较与原文依据' }).waitFor();
      await page.screenshot({ path: path.join(output, `comparison-${profile}.png`), fullPage: true });
      await noOverflow('comparison');
      await page.getByRole('button', { name: '文献问答', exact: true }).click();
      await page.getByRole('button', { name: '查找答案', exact: true }).click();
      await page.getByRole('heading', { name: '问答结果', exact: true }).waitFor();
      await page.getByRole('button', { name: '复制答案与来源', exact: true }).click();
      await page.getByRole('status').filter({ hasText: '已复制答案与来源。' }).waitFor();
      assert.match(await page.evaluate(() => window.__copiedAnswer), /local:demo-1/);
      checks.push({ profile, view: 'copy-sources', citationsPreserved: true });
      await nav('任务记录').click();
      await page.getByRole('heading', { name: '任务记录', exact: true }).waitFor();
      await page.getByText('处理未完成', { exact: true }).waitFor();
      await page.getByRole('button', { name: /确认后执行一个示例操作/ }).click();
      await page.getByRole('button', { name: '拒绝', exact: true }).click();
      await page.getByText('已拒绝示例操作。', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, `tasks-${profile}.png`), fullPage: true });
      await noOverflow('tasks');
      await nav('研究助手').click();
      assert.equal(await page.getByRole('textbox', { name: '研究问题' }).inputValue(), '比较数据划分和指标参数');
      await page.getByRole('heading', { name: '问答结果', exact: true }).waitFor();
      await nav('文献库').click();
      await page.getByRole('button', { name: titles[0], exact: true }).click();
      await page.getByRole('heading', { name: '一句话总结', exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, `reader-${profile}.png`), fullPage: true });
      await noOverflow('reader');
      await page.getByRole('button', { name: '生成复现方案', exact: true }).click();
      await page.getByRole('alert').waitFor();
      assert.equal(await page.getByRole('heading', { name: '一句话总结', exact: true }).isVisible(), true);
      checks.push({ profile, view: 'reader-error', contentPreserved: true });
      await nav('文献库').click();
      await page.getByRole('button', { name: titles[2], exact: true }).click();
      await page.getByText('尚未建立可用索引', { exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: '提问', exact: true }).count(), 0);
      checks.push({ profile, view: 'unindexed', askDisabled: true });
      await nav('发现论文').click();
      await page.getByPlaceholder('输入主题检索论文，如「图神经网络 推荐系统」…').fill('graph research');
      await page.locator('.discovery-view .lib-head button.primary').click();
      await page.locator('.discovery-result-card').waitFor();
      await page.screenshot({ path: path.join(output, `discovery-${profile}.png`), fullPage: true });
      await noOverflow('discovery');
      await page.locator('.discovery-result-card .modal-foot button.primary').click();
      await nav('文献库').click();
      await page.getByRole('button', { name: 'Newly Discovered Research Example', exact: true }).waitFor();
      checks.push({ profile, view: 'ingest', libraryRefreshed: true });
      await page.goto(base + '/?paper=local%3Ademo-1');
      await page.getByRole('heading', { name: titles[0], exact: true }).waitFor();
      await nav('发现论文').click(); await page.goBack();
      await page.getByRole('heading', { name: titles[0], exact: true }).waitFor();
      checks.push({ profile, view: 'deep-link-back', restored: true });
      await context.close();
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(output, 'validation.json'), JSON.stringify({ fixture: 'original synthetic offline UI responses', realModelCalls: 0, errors, checks, requests }, null, 2)); }
  console.log(JSON.stringify({ errors, checks, requestCount: requests.length, output }));
  assert.equal(errors.length, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
