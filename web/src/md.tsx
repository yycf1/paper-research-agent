import React from "react";

/**
 * 轻量 Markdown 渲染器（零依赖）。
 *
 * LLM 产物（精读报告、方向引导、问答）都是 Markdown，之前按 pre-wrap 纯文本
 * 展示，#/** 原样暴露。这里覆盖常用子集：标题、有序/无序列表、代码块、行内
 * 代码、粗体/斜体、链接、引用块；HTML 注释行（解析产物里的分页标记）直接丢弃。
 * 不追求完整规范——产物是模型按固定 prompt 生成的，结构可预期。
 */

// ---------- 行内 ----------

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // [文本](链接) | `代码` | **粗体** | *斜体*（含 _x_）
  const re = /(\[([^\]]+)\]\(([^)\s]+)\))|(`([^`]+)`)|(\*\*([^*]+)\*\*)|(\*([^*]+)\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const key = `${keyPrefix}-${i++}`;
    if (m[2] !== undefined) {
      nodes.push(
        <a key={key} href={m[3]} target="_blank" rel="noreferrer">{m[2]}</a>,
      );
    } else if (m[5] !== undefined) {
      nodes.push(<code key={key}>{m[5]}</code>);
    } else if (m[7] !== undefined) {
      nodes.push(<strong key={key}>{m[7]}</strong>);
    } else if (m[9] !== undefined) {
      nodes.push(<em key={key}>{m[9]}</em>);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// ---------- 块级 ----------

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "code"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "p"; text: string };

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      blocks.push({ kind: "p", text: para.join("\n") });
      para = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();
    // 解析产物里的分页/来源注释标记不展示
    if (/^<!--.*-->$/.test(t)) continue;
    if (t.startsWith("```")) {
      flushPara();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      blocks.push({ kind: "code", text: buf.join("\n") });
      continue;
    }
    const h = t.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      flushPara();
      blocks.push({ kind: "heading", level: h[1].length, text: h[2] });
      continue;
    }
    if (/^>\s?/.test(t)) {
      flushPara();
      blocks.push({ kind: "quote", text: t.replace(/^>\s?/, "") });
      continue;
    }
    if (/^[-*]\s+/.test(t)) {
      flushPara();
      const items: string[] = [];
      let j = i;
      while (j < lines.length) {
        const m2 = lines[j].trim().match(/^[-*]\s+(.*)$/);
        if (!m2) break;
        items.push(m2[1]);
        j++;
      }
      i = j - 1;
      blocks.push({ kind: "ul", items });
      continue;
    }
    if (/^\d+[.、)]\s+/.test(t)) {
      flushPara();
      const items: string[] = [];
      let j = i;
      while (j < lines.length) {
        const m2 = lines[j].trim().match(/^\d+[.、)]\s+(.*)$/);
        if (!m2) break;
        items.push(m2[1]);
        j++;
      }
      i = j - 1;
      blocks.push({ kind: "ol", items });
      continue;
    }
    if (t === "") {
      flushPara();
      continue;
    }
    para.push(t);
  }
  flushPara();
  return blocks;
}

export default function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const blocks = parseBlocks(text);
  return (
    <div className={`md ${className}`}>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "heading": {
            const Tag = (b.level <= 1 ? "h1" : b.level === 2 ? "h2" : b.level === 3 ? "h3" : "h4") as "h1";
            return <Tag key={i}>{renderInline(b.text, `h${i}`)}</Tag>;
          }
          case "code":
            return <pre key={i}><code>{b.text}</code></pre>;
          case "quote":
            return <blockquote key={i}>{renderInline(b.text, `q${i}`)}</blockquote>;
          case "ul":
            return (
              <ul key={i}>
                {b.items.map((it, j) => <li key={j}>{renderInline(it, `u${i}-${j}`)}</li>)}
              </ul>
            );
          case "ol":
            return (
              <ol key={i}>
                {b.items.map((it, j) => <li key={j}>{renderInline(it, `o${i}-${j}`)}</li>)}
              </ol>
            );
          default:
            return <p key={i}>{renderInline(b.text, `p${i}`)}</p>;
        }
      })}
    </div>
  );
}
