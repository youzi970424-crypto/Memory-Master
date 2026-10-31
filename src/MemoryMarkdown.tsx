import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function MemoryMarkdown({ content }: { content: string }) {
  return <div className="desktop-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      table: ({ children }) => <div className="desktop-markdown-table-wrap"><table className="desktop-markdown-table">{children}</table></div>,
      a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    }}>{content}</ReactMarkdown>
  </div>;
}
