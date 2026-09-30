'use client';

import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Model-written Markdown (the analysis, chat replies), styled for the dark
 * theme. Raw HTML in the text is shown as text, not rendered: react-markdown
 * only renders HTML when a plugin is added for it, which matters for text a
 * model wrote. remark-gfm adds tables, which chat replies use.
 */
const blocks: Components = {
  h1: ({ children }) => <h3 className="mb-2 mt-5 text-lg font-semibold text-white first:mt-0">{children}</h3>,
  h2: ({ children }) => <h4 className="mb-2 mt-5 text-base font-semibold text-white first:mt-0">{children}</h4>,
  h3: ({ children }) => <h5 className="mb-1 mt-4 font-semibold text-white first:mt-0">{children}</h5>,
  p: ({ children }) => <p className="my-3 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
  ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  hr: () => <hr className="my-5 border-white/10" />,
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-white/20 pl-4 text-gray-400">{children}</blockquote>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent-blue underline underline-offset-2">
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[0.9em] text-green-300">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="my-3 overflow-x-auto rounded-lg bg-black/50 p-3 text-sm [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  // Wide tables scroll inside their own box rather than stretching the page.
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto">
      <table className="w-full border-collapse text-left">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="whitespace-nowrap border-b border-white/15 px-3 py-2 font-medium text-gray-400">{children}</th>
  ),
  td: ({ children }) => <td className="border-b border-white/5 px-3 py-2 align-top">{children}</td>,
};

// For one-line items inside an existing list: no paragraph wrapper, so the
// text sits beside its bullet.
const inline: Components = { ...blocks, p: ({ children }) => <>{children}</> };

interface Props {
  children: string;
  className?: string;
  /** Render a single line without paragraph spacing. */
  inline?: boolean;
}

export default function Markdown({ children, className = '', inline: isInline = false }: Props) {
  const Wrapper = isInline ? 'span' : 'div';
  return (
    <Wrapper className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={isInline ? inline : blocks}>
        {children}
      </ReactMarkdown>
    </Wrapper>
  );
}
