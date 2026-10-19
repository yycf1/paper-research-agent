/* Offline browser workflow checks. Every API request uses original synthetic fixtures. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const output = path.resolve(process.env.WEB_QA_OUTPUT || 'data/web-qa');
fs.mkdirSync(output, { recursive: true });
const base = process.env.WEB_QA_URL || 'http://127.0.0.1:18809';
const titles = ['Evidence-aware Research Question Answering', 'Graph Learning with Explicit Evaluation Protocols', 'Vision Models: A Local Reading Example'];
function paper(n) { return { source_id: `local:demo-${n}`, source: 'local', title: titles[n - 1] || ({ 4: 'Newly Discovered Research Example', 5: 'Restricted Download Example', 6: 'No Fulltext Candidate Example', 7: 'Metadata Bookmark Example', 8: 'Legacy Server Response Example' })[n], authors: ['原创界面示例'], year: 2024 + n % 2, doi: null, citations: null, has_pdf: n !== 6, has_local_file: n === 1 || n === 3, local_file_type: n === 1 || n === 3 ? 'pdf' : null, landing_page: null, status: n >= 4 ? 'discovered' : n === 3 ? 'parsed' : 'indexed', error: null, added_at: '2026-10-14T10:00:00', tag: n === 3 ? '视觉研究' : '检索与智能体', abstract: '原创离线界面示例，仅用于验证交互。' }; }
function fields(p) {
  const fact = (field, value) => ({ field, raw_value: value, normalized_value: value, citation_status: 'valid', support_status: 'unverified', claim_sha256: `fixture-${p.source_id}-${field}`, issues: [], review: null, evidence: [{ paper_id: p.source_id, chunk_id: `${p.source_id}::0`, section: '4 Experiments', page: 3, quote: 'Synthetic-A uses a fixed test split; Recall@10 is reported as a ratio.' }] });
  return { schema_version: 1, paper_id: p.source_id, source_version: 'original-offline-fixture', fields: { research_goal: [fact('research_goal', '研究带证据的文献问答')], datasets: [fact('datasets', { name: 'Synthetic-A', split: 'fixed test split' })], metrics: [fact('metrics', { name: 'Recall@10', value: '0.5', task: 'retrieval', dataset: 'Synthetic-A', split: 'fixed test split', definition: 'recall at 10', unit: 'ratio', evaluation_protocol: 'full candidate set', model: 'Demo' })] }, omitted: {}, unmatched_reviews: 0, load_issues: [], numeric_comparison: { ranking_allowed: false, reason: '条件仍需核对' } };
}
function detail(p) { return { ...p, experiment: { research_goal: '研究带证据的文献问答', method: { name: 'Demo' }, datasets: [], metrics: [], generated_at: '2026-10-14T10:00:00' }, report: '## 一句话总结\n这是一份原创离线阅读示例。\n\n## 实验设计\nSynthetic-A 固定测试划分，报告 Recall@10；尚需核对原文依据。', repro_plan: null, full_text: '## Method\nOriginal synthetic text for browser verification.\n\n## Experiments\nSynthetic-A uses a fixed test split.', index_state: { status: p.status === 'indexed' ? 'ready' : 'unindexed', chunk_count: p.status === 'indexed' ? 2 : 0, can_ask: p.status === 'indexed' }, knowledge_current: true }; }
function demoRun(failed) {
  return { id: failed ? 'offline-failure' : 'offline-success', task: failed ? '离线失败回放示例' : '离线成功回放示例', status: 'done', pending_confirm: null, error: '', stopped_reason: 'final',
    answer: failed ? '所选资料尚未索引，无法给出指标。' : '原创示例指标为 0.5 [1]。', step_count: 1, duration_ms: 1234, usage: { prompt: 11, completion: 7 }, provenance: '脚本化离线示例，用于界面测试',
    verification: { status: failed ? 'failed' : 'passed', checks: [{ kind: 'evidence_answer', passed: !failed, detail: failed ? '所选论文尚未建立索引' : '引用结构通过，语义仍需核对' }], note: '按所选条件核对，不能代表全部语义正确。' },
    steps: [{ step: 1, tool: 'ask_library', arguments: { question: '示例指标' }, status: 'ok', detail: failed ? '尚无可检索索引块' : '已取得示例证据', duration_ms: 100, observation: failed ? {} : { sources: [{ paper_id: 'local:demo-1', title: titles[0], section: 'Results', n: [1], chunks: [{ n: 1, chunk_id: 'local:demo-1::0', page: 1, text: 'Original fixture metric is 0.5.' }] }] } }],
    telemetry: { call_count: 1, known_usage_calls: 0, unknown_usage_calls: 1, usage_complete: false, prompt: 11, completion: 7, embedding_tokens: 0, rerank_tokens: 0, cost_note: '未配置价格，未估算费用',
      calls: [{ id: 1, kind: 'rerank', stage: 'retrieval.rerank', model: 'offline-fixture', status: 'ok', elapsed_ms: 100, usage_known: false, usage: {} }] } };
}
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_BROWSER_PATH ? { executablePath: process.env.PLAYWRIGHT_BROWSER_PATH } : {}) });
  const checks = [], errors = [], requests = [];
  try {
    for (const [profile, width, height] of [['desktop', 1440, 1000], ['desktop-1280', 1280, 900]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      const page = await context.newPage();
      await page.addInitScript(() => { Object.defineProperty(navigator, 'clipboard', { value: { writeText: async text => { window.__copiedAnswer = text; } } }); });
      let papers = [paper(1), paper(2), paper(3)];
      const sessions = [], messages = {}, scopes = {};
      const createSession = title => { const s = { id: `session-${sessions.length + 1}`, title, updated_at: '2026-10-16T10:00:00', message_count: 0 }; sessions.unshift(s); messages[s.id] = []; return s; };
      let pending = true;
      let submitted = null, submittedPending = true, failDeleteOnce = true;
      let downloadJob = null;
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
        if (name === '/status') return respond({ version: 'demo', total: papers.length, counts: papers.reduce((counts, p) => ({ ...counts, [p.status]: (counts[p.status] || 0) + 1 }), {}), vector_chunks: 6 });
        if (name === '/settings') return respond({ base_url: 'https://example.invalid/v1', model: 'offline-fixture', api_key_masked: '', has_key: true, theme: 'light' });
        if (name === '/maintenance/cleanup') return respond({ items: [] });
        if (name === '/agent/demos') return respond({ demos: [demoRun(false), demoRun(true)] });
        if (name === '/tags') return respond({ tags: [{ name: '检索与智能体', count: 2 }, { name: '视觉研究', count: 1 }], sources: { local: papers.length }, untagged: 0 });
        if (name === '/papers' && method === 'GET') {
          const query = (url.searchParams.get('q') || '').toLowerCase();
          const selected = papers.filter(p => p.title.toLowerCase().includes(query) && (!url.searchParams.get('status') || p.status === url.searchParams.get('status')) && (!url.searchParams.get('tag') || p.tag === url.searchParams.get('tag')));
          return respond({ papers: selected, total: selected.length });
        }
        if (name === '/sessions' && method === 'GET') return respond({ sessions });
        if (name === '/sessions' && method === 'POST') return respond(createSession(body.title), 201);
        if (name.startsWith('/sessions/') && name.endsWith('/messages')) { const sid = name.split('/')[2]; return respond({ session_id: sid, paper_ids: scopes[sid] ?? null, messages: messages[sid] ?? [] }); }
        if (name === '/chat') {
          const sid = body.session_id || createSession(body.message).id;
          scopes[sid] = body.paper_ids;
          assert.deepEqual(body.paper_ids, ['local:demo-1', 'local:demo-2']);
          const comparison = body.action === 'compare' ? { papers: papers.slice(0, 2).map(p => ({ paper_id: p.source_id, title: p.title, field_evidence: fields(p) })), comparability: { pairs: [{ paper_ids: body.paper_ids, metrics: [{ left_metric: 'Recall@10', right_metric: 'Recall@20', missing_conditions: ['评估协议'], different_conditions: ['指标名称/参数'] }] }] } } : null;
          const reply = comparison ? '所选论文比较记录。' : '使用 Synthetic-A 的固定测试划分 [1]。';
          const sources = comparison ? [] : [{ n: [1], paper_id: 'local:demo-1', title: titles[0], section: '4 Experiments', page: 3, chunks: [{ n: 1, chunk_id: 'local:demo-1::0', page: 3, text: 'Synthetic-A uses a fixed test split.' }] }];
          messages[sid].push({ role: 'user', content: body.message, notes: [], details: { paper_ids: body.paper_ids, action: body.action } }, { role: 'assistant', content: reply, sources, notes: [], details: { paper_ids: body.paper_ids, comparison } });
          sessions.find(s => s.id === sid).message_count = messages[sid].length;
          return respond({ session_id: sid, intent: comparison ? 'compare' : 'qa', reply, sources, comparison, notes: [], new_papers: [] });
        }
        const submittedTask = submitted && { ...task, id: 'submitted-demo', task: submitted, status: submittedPending ? 'waiting_confirm' : 'done', pending_confirm: submittedPending ? { count: 1, titles: ['离线示例操作'] } : null, answer: submittedPending ? '' : '测试任务确认已拒绝。' };
        if (name === '/agent/tasks' && method === 'GET') return respond({ tasks: submittedTask ? [submittedTask, task] : [task] });
        if (name === '/agent/tasks' && method === 'POST') {
          submitted = body.task;
          if (body.session_id) { scopes[body.session_id] = body.allowed_paper_ids; messages[body.session_id].push({ role: 'user', content: body.task, notes: [] }, { role: 'assistant', content: '', notes: [], details: { task_id: 'submitted-demo', action: 'execute' } }); }
          return respond({ ...task, id: 'submitted-demo', task: submitted, status: 'running', pending_confirm: null, answer: '' });
        }
        if (name === '/agent/tasks/submitted-demo/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: ' + JSON.stringify({ type: 'snapshot', ...submittedTask }) + '\n\n' });
        if (name === '/agent/tasks/submitted-demo/confirm') { assert.equal(body.approve, false); submittedPending = false; return respond({ ok: true }); }
        if (name === '/agent/tasks/submitted-demo') return respond(submittedTask);
        if (name === '/agent/tasks/waiting-demo/confirm') { assert.equal(body.approve, false); pending = false; return respond({ ok: true }); }
        if (name === '/agent/tasks/waiting-demo') return respond(task);
        if (name === '/jobs') return respond({ jobs: [{ id: 'process-demo', kind: 'process_paper', status: 'done', result: { source_id: 'local:demo-3', final_status: 'parsed', stages: [{ stage: 'report', label: '生成精读报告', status: 'failed', detail: '离线失败场景示例' }] }, error: '' }, ...(downloadJob ? [downloadJob] : [])] });
        if (name === '/search') { const candidates = (body.query.includes('legacy') ? [8] : body.query.includes('bookmark') ? [7] : [4, 5, 6]).map(n => ({ ...paper(n), duplicate_of: null })); return respond({ candidates, total_hits: candidates.length, notes: [], source_hits: { openalex: candidates.length }, topics: [body.query], optimized: false }); }
        if (name === '/papers/ingest') {
          papers.push(...body.papers.map(p => ({ ...p, tag: body.tag })));
          if (body.papers.some(p => p.source_id === 'local:demo-8')) return respond({ ingested: 1, duplicates: 0, papers: body.papers });
          if (body.download_fulltext) downloadJob = { id: 'download-demo', kind: 'download_papers', status: 'running', error: '', result: { total: body.papers.length, completed: 0, items: body.papers.map(p => ({ source_id: p.source_id, title: p.title, status: 'pending', detail: '等待下载' })) } };
          return respond({ ingested: body.papers.length, duplicates: 0, papers: body.papers, download_job: body.download_fulltext ? downloadJob : null });
        }
        if (name === '/jobs/download-demo') {
          downloadJob.status = 'done'; downloadJob.result.completed = downloadJob.result.total;
          downloadJob.result.items = downloadJob.result.items.map(item => ({ ...item, status: item.source_id.endsWith('4') ? 'saved' : item.source_id.endsWith('5') ? 'failed' : 'no_link', detail: item.source_id.endsWith('4') ? '全文已保存；尚未分析' : item.source_id.endsWith('5') ? 'HTTP 403 source refused' : '来源没有提供全文候选链接' }));
          papers = papers.map(p => p.source_id === 'local:demo-4' ? { ...p, has_local_file: true, status: 'downloaded' } : p.source_id === 'local:demo-5' ? { ...p, status: 'download_failed' } : p);
          return respond(downloadJob);
        }
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
          if (method === 'DELETE') {
            if (sid === 'local:demo-2' && failDeleteOnce) { failDeleteOnce = false; return respond({ detail: '原创离线删除失败场景' }, 503); }
            papers = papers.filter(row => row.source_id !== sid);
            return respond({ ok: true, source_id: sid });
          }
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
      const linkedOnly = page.locator('.library-view tr').filter({ has: page.getByRole('button', { name: titles[1], exact: true }) });
      await linkedOnly.getByText('未保存', { exact: true }).waitFor();
      await linkedOnly.getByText('有来源链接', { exact: true }).waitFor();
      assert.equal(await page.locator('.library-view').getByText('处理阶段（全库数量，非分组）', { exact: true }).isVisible(), true);
      checks.push({ profile, view: 'file-versus-link', localFileDistinct: true });
      await page.locator('.library-view .lib-q').fill('unmatched-fixture');
      await page.getByText('没有符合筛选条件的论文', { exact: true }).waitFor();
      await page.locator('.library-view .lib-q').fill('');
      await page.getByRole('button', { name: titles[0], exact: true }).waitFor();
      await page.getByRole('checkbox', { name: `选择 ${titles[0]}`, exact: true }).check();
      await page.getByRole('checkbox', { name: `选择 ${titles[1]}`, exact: true }).check();
      await page.getByRole('button', { name: '比较所选论文', exact: true }).click();
      await page.getByRole('textbox', { name: '研究问题' }).fill('比较数据划分和指标参数');
      await page.getByRole('button', { name: '开始比较', exact: true }).click();
      await page.getByRole('heading', { name: '论文比较与原文依据' }).waitFor();
      await page.screenshot({ path: path.join(output, `comparison-${profile}.png`), fullPage: true });
      await noOverflow('comparison');
      assert.equal(await page.getByRole('combobox', { name: '本次操作', exact: true }).inputValue(), 'chat');
      await page.getByRole('textbox', { name: '研究问题' }).fill('这些论文的数据如何划分？');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText('使用 Synthetic-A 的固定测试划分 [1]。', { exact: true }).waitFor();
      await page.getByRole('button', { name: '复制答案与来源', exact: true }).last().click();
      await page.getByRole('status').filter({ hasText: '已复制答案与来源。' }).waitFor();
      assert.match(await page.evaluate(() => window.__copiedAnswer), /local:demo-1/);
      await page.locator('.research-turn.bot').last().getByText('查看原文证据（1 段）', { exact: true }).click();
      const originalLink = page.getByRole('link', { name: '打开原 PDF 第 3 页', exact: true });
      await originalLink.waitFor(); assert.match(await originalLink.getAttribute('href'), /local%3Ademo-1\/file#page=3$/);
      checks.push({ profile, view: 'copy-sources', citationsPreserved: true });
      await nav('任务记录').click();
      await page.getByRole('heading', { name: '任务记录', exact: true }).waitFor();
      await page.getByText('处理未完成', { exact: true }).waitFor();
      await page.getByRole('button', { name: /确认后执行一个示例操作/ }).click();
      await page.getByRole('button', { name: '拒绝', exact: true }).click();
      await page.getByText('已拒绝示例操作。', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, `tasks-${profile}.png`), fullPage: true });
      await noOverflow('tasks');
      const submissionsBeforeReplay = requests.filter(r => r.method === 'POST' && r.path === '/agent/tasks').length;
      await page.getByRole('button', { name: /离线成功回放示例/ }).click();
      await page.locator('.task-detail').getByText('目标已核验', { exact: true }).waitFor();
      await page.locator('.task-detail').getByText('查看原文证据（1 段）', { exact: true }).click();
      await page.getByText('Original fixture metric is 0.5.', { exact: true }).waitFor();
      await page.locator('.task-detail').getByText('调用明细与统计范围 · 1 次', { exact: true }).click();
      await page.locator('.task-detail').getByText('未知', { exact: true }).waitFor();
      const download = page.waitForEvent('download');
      await page.locator('.task-detail').getByRole('button', { name: '导出记录', exact: true }).click();
      assert.match((await download).suggestedFilename(), /offline-success/);
      checks.push({ profile, view: 'goal-review', evidenceVisible: true, unknownUsageVisible: true, exportWorks: true });
      await page.getByRole('button', { name: /离线失败回放示例/ }).click();
      await page.locator('.task-detail').getByText('目标未达成', { exact: true }).waitFor();
      await page.getByText('所选论文尚未建立索引', { exact: true }).waitFor();
      assert.equal(requests.filter(r => r.method === 'POST' && r.path === '/agent/tasks').length, submissionsBeforeReplay);
      await noOverflow('failed-demo-review');
      checks.push({ profile, view: 'failed-goal-review', honestFailure: true, replayDoesNotExecute: true });
      await nav('研究助手').click();
      assert.equal(await page.getByRole('textbox', { name: '研究问题' }).inputValue(), '');
      await page.getByText('使用 Synthetic-A 的固定测试划分 [1]。', { exact: true }).waitFor();
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
      const downloadProgress = page.getByRole('region', { name: '所选论文下载进度' });
      await downloadProgress.getByText('所选论文下载结果', { exact: true }).waitFor();
      await downloadProgress.getByText('全文已保存', { exact: true }).waitFor();
      await downloadProgress.getByText('下载失败', { exact: true }).waitFor();
      await downloadProgress.getByText('没有全文链接', { exact: true }).waitFor();
      assert.equal(requests.filter(r => r.path === '/papers/ingest').at(-1).body.download_fulltext, true);
      await page.screenshot({ path: path.join(output, `download-results-${profile}.png`), fullPage: true });
      await noOverflow('save-and-download');
      await page.getByPlaceholder('输入主题检索论文，如「图神经网络 推荐系统」…').fill('bookmark only');
      await page.locator('.discovery-view .lib-head button.primary').click();
      await page.getByRole('button', { name: '仅保存信息', exact: true }).click();
      await page.locator('.discovery-result-card').waitFor({ state: 'hidden' });
      assert.equal(requests.filter(r => r.path === '/papers/ingest').at(-1).body.download_fulltext, false);
      assert.equal(papers.find(p => p.source_id === 'local:demo-7').has_local_file, false);
      assert.equal(await downloadProgress.count(), 0);
      checks.push({ profile, view: 'save-information-only', noDownloadSubmitted: true });
      await page.getByPlaceholder('输入主题检索论文，如「图神经网络 推荐系统」…').fill('legacy server');
      await page.locator('.discovery-view .lib-head button.primary').click();
      await page.locator('.discovery-result-card .modal-foot button.primary').click();
      await page.getByText(/当前服务仅保存了信息，未提交下载任务/).waitFor();
      checks.push({ profile, view: 'legacy-ingest-response', noFalseDownloadClaim: true });
      await nav('文献库').click();
      await page.getByRole('button', { name: 'Newly Discovered Research Example', exact: true }).waitFor();
      checks.push({ profile, view: 'ingest', libraryRefreshed: true });
      await nav('任务记录').click();
      await page.getByText('部分未取得全文', { exact: true }).waitFor();
      await page.locator('details.processing-record').filter({ hasText: '获取所选全文' }).locator('summary').click();
      await page.getByRole('region', { name: '所选论文下载进度' }).getByText('没有全文链接', { exact: true }).waitFor();
      await noOverflow('download-task-record');
      await page.goto(base + '/?paper=local%3Ademo-1');
      await page.getByRole('heading', { name: titles[0], exact: true }).waitFor();
      await nav('发现论文').click(); await page.goBack();
      await page.getByRole('heading', { name: titles[0], exact: true }).waitFor();
      checks.push({ profile, view: 'deep-link-back', restored: true });

      await nav('研究助手').click();
      const historyPane = page.getByRole('complementary', { name: '研究历史' });
      await historyPane.locator('.execution-history > summary').click();
      await historyPane.getByRole('button', { name: /确认后执行一个示例操作/ }).click();
      await page.locator('.conversation-task').getByText('已拒绝示例操作。', { exact: true }).waitFor();
      const paneBox = await historyPane.boundingBox(), chatBox = await page.locator('.assistant .chat').boundingBox();
      assert.ok(paneBox.x + paneBox.width <= chatBox.x + 1);
      await noOverflow('agent-history-left');
      await page.screenshot({ path: path.join(output, `agent-history-${profile}.png`), fullPage: true });
      await historyPane.getByRole('button', { name: '＋ 新对话', exact: true }).click();
      await page.getByRole('combobox', { name: '本次操作', exact: true }).selectOption('execute');
      await page.locator('.execution-options > summary').click();
      await page.getByRole('combobox', { name: '结果验收方式', exact: true }).selectOption('preview');
      await page.getByRole('textbox', { name: '研究问题' }).fill('离线提交的目标');
      await page.getByRole('button', { name: '提交执行', exact: true }).click();
      await page.locator('.assistant .confirm-box').waitFor();
      await historyPane.getByRole('button', { name: /确认后执行一个示例操作/ }).click();
      await page.locator('.conversation-task').getByText('已拒绝示例操作。', { exact: true }).waitFor();
      await page.getByRole('button', { name: '查看进度', exact: true }).waitFor();
      assert.equal(await historyPane.getByRole('button', { name: '＋ 新对话', exact: true }).isDisabled(), false);
      await page.waitForTimeout(1500); // Allow the SSE fallback poll to arrive while viewing history.
      assert.equal(await page.locator('.conversation-task').getByText('已拒绝示例操作。', { exact: true }).isVisible(), true);
      await page.getByRole('button', { name: '查看进度', exact: true }).click();
      await page.getByRole('button', { name: '拒绝', exact: true }).click();
      await page.getByText('测试任务确认已拒绝。', { exact: true }).waitFor();
      await historyPane.getByRole('button', { name: '＋ 新对话', exact: true }).click();
      checks.push({ profile, view: 'running-task-history', historyDoesNotInterrupt: true });

      await page.getByRole('textbox', { name: '研究问题' }).fill('查找两篇综述并保存题录');
      const submitsBeforeHandoff = requests.filter(r => r.method === 'POST' && r.path === '/agent/tasks').length;
      const beforeLayout = await page.locator('.assistant .chat').boundingBox();
      await page.getByRole('combobox', { name: '本次操作', exact: true }).selectOption('execute');
      assert.equal(await page.getByRole('textbox', { name: '研究问题' }).inputValue(), '查找两篇综述并保存题录');
      const afterLayout = await page.locator('.assistant .chat').boundingBox();
      assert.deepEqual(beforeLayout, afterLayout);
      assert.equal(requests.filter(r => r.method === 'POST' && r.path === '/agent/tasks').length, submitsBeforeHandoff);
      checks.push({ profile, view: 'chat-task-handoff', prefillOnly: true });

      await nav('文献库').click();
      await page.getByRole('checkbox', { name: `选择 ${titles[0]}`, exact: true }).check();
      await page.getByRole('checkbox', { name: `选择 ${titles[1]}`, exact: true }).check();
      await page.locator('.library-view .lib-q').fill('Vision Models');
      await page.getByRole('button', { name: titles[0], exact: true }).waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: '删除所选论文', exact: true }).click();
      const deletion = page.getByRole('dialog', { name: '删除所选 2 篇论文？' });
      await deletion.getByText(titles[0], { exact: true }).waitFor();
      await deletion.getByText(titles[1], { exact: true }).waitFor();
      const deletesBeforeCancel = requests.filter(r => r.method === 'DELETE').length;
      await deletion.getByRole('button', { name: '取消', exact: true }).click();
      assert.equal(requests.filter(r => r.method === 'DELETE').length, deletesBeforeCancel);
      checks.push({ profile, view: 'bulk-delete-cancel', hiddenSelectionVisible: true, noMutation: true });
      await page.locator('.library-view .lib-q').fill('');
      await page.getByRole('button', { name: titles[0], exact: true }).waitFor();
      await page.getByRole('button', { name: '删除所选论文', exact: true }).click();
      await page.getByRole('button', { name: '确认删除', exact: true }).click();
      await page.getByText('已删除 1 篇；1 篇未删除，已保留勾选，可重试。', { exact: false }).waitFor();
      await page.getByRole('button', { name: titles[0], exact: true }).waitFor({ state: 'hidden' });
      assert.equal(await page.getByRole('checkbox', { name: `选择 ${titles[1]}`, exact: true }).isChecked(), true);
      checks.push({ profile, view: 'bulk-delete-partial', failedSelectionRetained: true });
      await page.getByRole('button', { name: '删除所选论文', exact: true }).click();
      await page.getByRole('button', { name: '确认删除', exact: true }).click();
      await page.getByRole('button', { name: titles[1], exact: true }).waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: titles[2], exact: true }).waitFor();
      assert.equal(papers.length, 6);
      checks.push({ profile, view: 'bulk-delete-retry', unselectedRetained: true });
      await context.close();
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(output, 'validation.json'), JSON.stringify({ fixture: 'original synthetic offline UI responses', realModelCalls: 0, errors, checks, requests }, null, 2)); }
  console.log(JSON.stringify({ errors, checks, requestCount: requests.length, output }));
  assert.equal(errors.length, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
