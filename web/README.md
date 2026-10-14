# Paper Research Agent Web

个人文献库智能体的 React + TypeScript 前端。包含文献库、论文详情、对话、Agent 任务、字段证据表和本地模型设置。

## 本地开发

要求 Node.js 20.19+ 或 22.12+。先在项目根目录运行 `uv run pa serve`，然后：

```bash
cd web
npm ci
npm run dev
```

Vite 默认监听 5173，`/api` 代理到 `http://127.0.0.1:8000`，可在 `vite.config.ts` 修改。

```bash
npm run build
npm run lint
```

构建结果写入 `web/dist/`，由 FastAPI 同端口托管。仓库保留构建产物；发布前端改动时同步构建。既有 React lint 警告在[验证记录](../docs/validation.md)中说明。

## 代码入口

| 路径 | 职责 |
| --- | --- |
| `src/App.tsx` | 页面切换、深链和全局状态 |
| `src/pages/Library.tsx` | 检索预览、筛选与文献管理 |
| `src/components/PaperDetail.tsx` | 文献处理、报告、字段依据与复现产物 |
| `src/pages/Assistant.tsx` | 会话、Agent 任务、SSE 与轮询回退 |
| `src/components/FieldEvidence.tsx` | 三字段比较、待核对状态与原文展开 |
| `src/api.ts` | 后端类型与请求封装 |

自动匹配到的原文是候选依据，页面中的“待核对”不代表事实得到支持。更多说明见[项目首页](../README.md)。
