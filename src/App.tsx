import { QuickEntrySetting, quickShortcut } from './QuickEntry';
import MemoryMarkdown from './MemoryMarkdown';
import { invoke } from '@tauri-apps/api/core';
import { emitTo, listen } from '@tauri-apps/api/event';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { Ring } from 'ldrs/react';
import 'ldrs/react/Ring.css';
import { CSSProperties, FormEvent, KeyboardEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { api, type ApiConfig, type AuthSession, type AuthUser, type ChatResult, type MemoryCard, type MemoryFolder, type MemoryType } from './api';
import addMemoryIcon from './assets/home/add-memory.svg';
import bookOpenIcon from './assets/home/book-open.svg';
import calendarCheckIcon from './assets/home/calendar-check.svg';
import cardFolderIcon from './assets/home/card-folder.svg';
import cardNoteIcon from './assets/home/card-note.svg';
import cardOpenArrowIcon from './assets/home/card-open-arrow.svg';
import chevronLeftIcon from './assets/home/chevron-left.svg';
import chevronRightIcon from './assets/home/chevron-right.svg';
import circleAlertIcon from './assets/home/circle-alert.svg';
import copyIcon from './assets/home/copy.svg';
import folderClosedIcon from './assets/home/folder-closed.svg';
import homeIconCases from './assets/home/home-icon-cases.png';
import homeIconCustom from './assets/home/home-icon-custom.png';
import homeIconKnowledge from './assets/home/home-icon-knowledge.png';
import homeIconMeetings from './assets/home/home-icon-meetings.png';
import homeIconTodos from './assets/home/home-icon-todos.png';
import logoImage from './assets/home/logo.png';
import loginBackgroundVideo from './assets/auth/login-background.mp4';
import memoryFolderCasesIcon from './assets/home/memory-folder-cases.svg';
import memoryFolderCustomIcon from './assets/home/memory-folder-custom.svg';
import memoryFolderKnowledgeIcon from './assets/home/memory-folder-knowledge.svg';
import memoryFolderMeetingsIcon from './assets/home/memory-folder-meetings.svg';
import memoryFolderTodosIcon from './assets/home/memory-folder-todos.svg';
import navCardsIcon from './assets/home/nav-cards.svg';
import navHistoryIcon from './assets/home/nav-history.svg';
import navHistoryChatIcon from './assets/home/nav-history-chat.svg';
import navRecognizeIcon from './assets/home/nav-recognize.svg';
import navSettingsIcon from './assets/home/nav-settings.svg';
import panelLeftIcon from './assets/home/panel-left.svg';
import pickerCheckIcon from './assets/home/picker-check.svg';
import pickerFolderOpenIcon from './assets/home/picker-folder-open.svg';
import pickerPlayingCardsFanIcon from './assets/home/picker-playing-cards-fan.svg';
import pickerSearchIcon from './assets/home/picker-search.svg';
import sendIcon from './assets/home/send.svg';
import squarePlusIcon from './assets/home/square-plus.svg';
import penLineIcon from './assets/home/pen-line.svg';
import trashIcon from './assets/home/trash.svg';
import presentationIcon from './assets/home/presentation.svg';
import usersIcon from './assets/home/users.svg';

type View = 'home' | 'memories' | 'memory-detail' | 'history' | 'chat';
type ModalMode = 'add' | 'save' | 'edit';
type FolderModalMode = 'create' | 'rename';
type HomeMemoryGroup = string;
type MemoryPickerTarget = 'home' | 'chat';
type ConfigTab = 'chat' | 'embedding';
type AuthMode = 'login' | 'register';

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  createdAt: string;
  memoryIds?: string[];
  failed?: boolean;
  citationMemoryIds?: string[];
  autoRecalledMemoryIds?: string[];
  memoryUsage?: ChatResult['memory_usage'];
};

type ChatSession = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  citationCount: number;
  citedMemoryIds: string[];
  manualMemoryIds: string[];
  lastSummary: string;
  lastRecommendation: string;
  messages: ChatMessage[];
  lastResult: ChatResult | null;
};

type ChatContextMenu = {
  sessionId: string;
  x: number;
  y: number;
};

type MemoryFolderContextMenu = {
  folder: MemoryFolder;
  x: number;
  y: number;
};

type MemoryUploadToast = {
  title: string;
  folderName: string;
  icon: string;
  accent: 'blue' | 'orange' | 'red' | 'green';
};

declare global {
  interface Window {
    __MEMORY_MASTER_QUICK_ADD__?: boolean;
    __TAURI_INTERNALS__?: unknown;
  }
}

const CHAT_STORAGE_KEY = 'memory-master.chat-sessions.v1';
const HOME_FOLDER_STORAGE_KEY = 'memory-master.home-folders.v1';
const AUTH_STORAGE_KEY = 'memory-master.auth-session.v1';
const MAX_MANUAL_MEMORY_IDS = 5;
const MIN_HOME_FOLDERS = 1;
const MAX_HOME_FOLDERS = 4;
const MEMORY_RECALL_ENABLED = true;
const DEFAULT_HOME_FOLDER_NAMES = [
  '客户案例',
  '会议纪要',
  '日常待办',
  '知识沉淀',
];
const HOME_PROMPT = '把工作交给记忆大师，让决策有据可依';
const HOME_TITLE = 'HI，我是';
const HOME_SLOGAN = '请选择你需要的卡片类型，唤醒你的工作记忆';
const MEMORY_PICKER_LIMIT_TOOLTIP = '为保证结果的准确性，最多可添加5张记忆卡片';
const CHAT_LOADING_TEXT = '认真思考中，请稍后...';
const UNVERIFIED_MEMORY_REFERENCE_NOTICE = '本轮模型没有返回可校验的记忆引用，以上建议的记忆依据未被确认。';
const navItems: Array<{
  label: string;
  view: View;
  action?: 'add-memory' | 'open-history';
  icon: string;
  iconClassName?: string;
  className: string;
  dividerAfter?: boolean;
}> = [
  { label: '提问', view: 'home', icon: navHistoryChatIcon, className: 'desktop-nav--new' },
  { label: '记忆卡', view: 'memories', icon: navCardsIcon, className: 'desktop-nav--cards' },
  { label: '历史', view: 'chat', action: 'open-history', icon: navHistoryIcon, className: 'desktop-nav--history', dividerAfter: true },
  { label: '添加', view: 'home', action: 'add-memory', icon: navRecognizeIcon, className: 'desktop-nav--recognize' },
];

const emptyConfig: ApiConfig = {
  provider: '',
  base_url: '',
  api_key: '',
  model: '',
  embedding_base_url: '',
  embedding_api_key: '',
  embedding_model: '',
  enabled: true,
};

const MASKED_CONFIG_SECRET = '********';

function configSecretInputValue(value: string) {
  return value === MASKED_CONFIG_SECRET ? '' : value;
}

function configForSubmit(config: ApiConfig, hasStoredChatApiKey: boolean, hasStoredEmbeddingApiKey: boolean): ApiConfig {
  return {
    ...config,
    base_url: config.base_url.trim() || 'https://api.deepseek.com',
    embedding_base_url: config.embedding_base_url.trim() || 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    embedding_model: config.embedding_model.trim() || 'text-embedding-v4',
    api_key: config.api_key || (hasStoredChatApiKey ? MASKED_CONFIG_SECRET : ''),
    embedding_api_key: config.embedding_api_key || (hasStoredEmbeddingApiKey ? MASKED_CONFIG_SECRET : ''),
    enabled: true,
  };
}

function nowIso() {
  return new Date().toISOString();
}

function modelErrorText(result: ChatResult) {
  switch (result.meta.error_code) {
    case 'model_connection_error':
      return '模型服务连接失败，请检查网络、Base URL 和模型名称后重试。';
    case 'model_timeout':
      return result.meta.error || '模型响应超时，请稍后重试或缩短上下文。';
    case 'model_http_error':
      return result.meta.error || '模型服务返回错误，请稍后重试。';
    case 'model_invalid_json':
      return '模型已返回内容，但格式无法解析，正在重试仍未成功。';
    case 'model_empty_response':
      return '模型服务已响应，但没有返回有效内容，请稍后重试。';
    case 'model_output_truncated':
      return result.meta.error || '模型输出达到长度上限，请缩短问题或减少引用内容后重试。';
    case 'model_response_invalid':
      return '模型返回格式异常，请稍后重试。';
    case 'model_config_missing':
      return result.meta.error || '模型配置不完整，请先完成配置。';
    case 'model_unavailable':
      return '模型服务暂时不可用，请稍后重试。';
    default:
      return result.meta.error || '模型调用失败，请检查配置后重试。';
  }
}

function formatUpdateDateTime(value: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function sessionTitle(query: string) {
  return query.length > 18 ? `${query.slice(0, 18)}...` : query;
}

function sessionPreview(session: ChatSession) {
  return session.lastSummary || session.messages.at(-1)?.text || '暂无摘要';
}

function safeStringArray(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function safeMemoryUsage(value: unknown) {
  if (!value || typeof value !== 'object') return undefined;
  const usage = value as Record<string, unknown>;
  const recalledMemories = Array.isArray(usage.recalled_memories)
    ? usage.recalled_memories
      .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === 'object')
      .map((entry) => ({
        ...entry,
        id: typeof entry.id === 'string' ? entry.id : '',
        title: typeof entry.title === 'string' ? entry.title : '未命名记忆卡',
        selection: entry.selection === 'manual' ? 'manual' : 'auto',
        citation_priority: entry.citation_priority === 'primary' ? 'primary' : 'secondary',
      }))
      .filter((entry) => entry.id)
    : [];
  const citations = Array.isArray(usage.citations)
    ? usage.citations
      .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === 'object')
      .map((entry) => ({
        ...entry,
        id: typeof entry.id === 'string' ? entry.id : '',
        title: typeof entry.title === 'string' ? entry.title : '未命名记忆卡',
        source: typeof entry.source === 'string' ? entry.source : '未知来源',
        keywords: safeStringArray(entry.keywords),
      }))
      .filter((entry) => entry.id)
    : [];
  const weeklyTodos = Array.isArray(usage.weekly_todos)
    ? usage.weekly_todos
      .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === 'object')
      .map((entry) => ({
        ...entry,
        memory_id: typeof entry.memory_id === 'string' ? entry.memory_id : '',
        text: typeof entry.text === 'string' ? entry.text : '',
        memory_title: typeof entry.memory_title === 'string' ? entry.memory_title : '未命名记忆卡',
        source: typeof entry.source === 'string' ? entry.source : '未知来源',
      }))
      .filter((entry) => entry.memory_id && entry.text)
    : [];

  return {
    ...usage,
    primary_citation_ids: safeStringArray(usage.primary_citation_ids),
    secondary_citation_ids: safeStringArray(usage.secondary_citation_ids),
    recalled_memories: recalledMemories,
    citations,
    weekly_todos: weeklyTodos,
  } as ChatResult['memory_usage'];
}

function safeChatMessage(item: unknown, index: number): ChatMessage | null {
  if (!item || typeof item !== 'object') return null;
  const message = item as Record<string, unknown>;

  return {
    id: typeof message.id === 'string' ? message.id : `legacy-message-${index}`,
    role: message.role === 'assistant' ? 'assistant' : 'user',
    text: typeof message.text === 'string' ? message.text : '',
    createdAt: typeof message.createdAt === 'string' ? message.createdAt : nowIso(),
    failed: message.failed === true || (
      message.role === 'assistant' && typeof message.text === 'string' && message.text.startsWith('调用失败')
    ),
    memoryIds: safeStringArray(message.memoryIds),
    citationMemoryIds: safeStringArray(message.citationMemoryIds),
    autoRecalledMemoryIds: safeStringArray(message.autoRecalledMemoryIds),
    memoryUsage: safeMemoryUsage(message.memoryUsage),
  };
}

function collectLines(items: unknown) {
  if (!Array.isArray(items)) return [] as string[];
  return items
    .map((item) => {
      if (!item || typeof item !== 'object') return '';
      const text = 'text' in item ? String((item as { text?: unknown }).text ?? '').trim() : '';
      if (!text) return '';
      return text;
    })
    .filter(Boolean);
}

function hasExplicitOutputFormat(query: string) {
  return /输出格式|按.+格式|用.+(数字|编号|序号|格式|展示)|按日期|按时间|分日期|序列号|编号|序号|按条|逐条|1\s*2\s*3|表格|多少条|几条|一共.{0,8}条|总共.{0,8}条|精简|简化|压缩|缩短|一句话|一段话|摘要|总结|只看|只基于|只围绕|不要发散|不要展开|保留重点|提炼重点/.test(query);
}

function isRevisionTurnType(turnType: string | undefined) {
  return turnType === 'format_revision' || turnType === 'constraint_revision';
}

function memoryUsageWithoutRecall(usage: ChatResult['memory_usage']) {
  return {
    ...usage,
    selected_memory_ids: [],
    auto_recalled_memory_ids: [],
    citation_count: 0,
    primary_citation_ids: [],
    secondary_citation_ids: [],
    recalled_memories: [],
    weekly_todos: [],
    citations: [],
  };
}

function answerText(result: ChatResult, query: string) {
  if (hasExplicitOutputFormat(query) && result.answer.recommendation.trim()) {
    return result.answer.recommendation.trim();
  }

  const sections: string[] = [];
  const actionLines = collectLines(result.answer.action_items);
  const uncertaintyLines = collectLines(result.answer.uncertainties)
    .filter((line) => line !== UNVERIFIED_MEMORY_REFERENCE_NOTICE);

  const mainText = result.answer.recommendation.trim() || result.answer.summary.trim();
  if (mainText) {
    sections.push(mainText);
  } else {
    const reasoningLines = collectLines(result.answer.reasoning_points);
    if (reasoningLines.length) sections.push(reasoningLines.join('；'));
  }

  if (actionLines.length) {
    sections.push(actionLines.join('；'));
  }

  const displayedLines = new Set(sections.flatMap((section) => section.split(/\n+/).map((line) => line.trim())));
  const additionalUncertainties = [...new Set(uncertaintyLines)].filter((line) => !displayedLines.has(line));
  if (additionalUncertainties.length) {
    sections.push(additionalUncertainties.join('；'));
  }

  return sections.join('\n\n');
}

function renderInlineMarkdown(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('__') && part.endsWith('__')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    return <span key={index}>{part}</span>;
  });
}

function splitTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function isTableDivider(line: string) {
  const cells = splitTableRow(line);
  return cells.length >= 2 && cells.every((cell) => /^:?[-–—―]{3,}:?$/.test(cell));
}

function isTableRow(line: string) {
  return line.includes('|') && splitTableRow(line).filter(Boolean).length >= 2;
}

function tableHeadersForRows(headers: string[], rows: string[][]) {
  const columnCount = Math.max(headers.length, ...rows.map((row) => row.length));
  if (headers.length === columnCount - 1 && rows.some((row) => /^\d+$/.test(row[0] ?? ''))) {
    return ['序号', ...headers];
  }
  return headers.length < columnCount
    ? [...headers, ...Array(columnCount - headers.length).fill('')]
    : headers;
}

function normalizeLooseMarkdownTables(content: string) {
  return content
    .replace(
      /([^\n|][^\n]*?)\s+(\|[^|\n]+(?:\|[^|\n]+){2,}\|)\s*\n\s*(\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*){2,}\|?)/g,
      '$1\n\n$2\n$3',
    )
    .replace(
      /([^\n])\s+(\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*){2,}\|?)/g,
      '$1\n$2',
    );
}

function normalizeLooseNumberedHeadings(content: string) {
  const numberedHeadingPattern = String.raw`\*\*\d+[.)]\s+[^*\n]{2,80}\*\*`;
  return content
    .replace(
      new RegExp(`([^\\n])\\s+(${numberedHeadingPattern})`, 'g'),
      '$1\n\n$2',
    )
    .replace(
      new RegExp(`(${numberedHeadingPattern})\\s+([^\\n])`, 'g'),
      '$1\n\n$2',
    );
}

function normalizeLooseChineseHeadings(content: string) {
  const chineseHeadingPattern = String.raw`[一二三四五六七八九十]+[、.．]\s*[^。！？；，、,.!?\n]{2,32}`;
  return content
    .replace(
      new RegExp(`([。！？；：:）)])\\s+(${chineseHeadingPattern})(?=\\s|$)`, 'g'),
      '$1\n\n$2',
    )
    .replace(
      new RegExp(`(^|\\n)(\\s*${chineseHeadingPattern})[ \\t]+(?=\\S)`, 'g'),
      '$1$2\n\n',
    );
}

function normalizeLooseBoldHeadings(content: string) {
  const boldHeadingPattern = String.raw`\*\*[^*\n。！？；，、,.!?]{2,24}\*\*`;
  return content.split('\n').map((line) => {
    if (line.includes('|')) return line;
    return line
      .replace(
        new RegExp(`(^|\\n)(\\s*${boldHeadingPattern})[ \\t]+(?=\\S)`, 'g'),
        '$1$2\n\n',
      )
      .replace(
        new RegExp(`([。！？；：:）)])\\s+(${boldHeadingPattern})`, 'g'),
        '$1\n\n$2',
      )
      .replace(
        new RegExp(`(${boldHeadingPattern})[ \\t]+(?=\\S)`, 'g'),
        '$1\n\n',
      );
  }).join('\n');
}

function decodeLooseJsonString(value: string) {
  try {
    return JSON.parse(`"${value}"`) as string;
  } catch {
    return value
      .replace(/\\n/g, '\n')
      .replace(/\\r/g, '\r')
      .replace(/\\t/g, '\t')
      .replace(/\\"/g, '"')
      .replace(/\\\\/g, '\\');
  }
}

function extractLooseJsonStringField(content: string, key: string) {
  const match = new RegExp(`"${key}"\\s*:\\s*"`).exec(content);
  if (!match) return '';
  const chars: string[] = [];
  let index = match.index + match[0].length;
  while (index < content.length) {
    const char = content[index];
    if (char === '\\' && index + 1 < content.length) {
      chars.push(content.slice(index, index + 2));
      index += 2;
      continue;
    }
    if (char === '"') {
      const tail = content.slice(index + 1);
      if (/^\s*(?:,\s*"[A-Za-z_][A-Za-z0-9_]*"\s*:|})/.test(tail)) break;
    }
    chars.push(char);
    index += 1;
  }
  return decodeLooseJsonString(chars.join('')).trim();
}

function unwrapLooseAnswerContent(content: string) {
  const trimmed = content.trim();
  if (!trimmed.startsWith('{') || !/"(?:summary|recommendation)"\s*:/.test(trimmed)) {
    return content;
  }
  try {
    const parsed = JSON.parse(trimmed) as { summary?: unknown; recommendation?: unknown };
    const summary = typeof parsed.summary === 'string' ? parsed.summary.trim() : '';
    const recommendation = typeof parsed.recommendation === 'string' ? parsed.recommendation.trim() : '';
    return [summary, recommendation].filter(Boolean).join('\n\n') || content;
  } catch {
    const summary = extractLooseJsonStringField(trimmed, 'summary');
    const recommendation = extractLooseJsonStringField(trimmed, 'recommendation');
    return [summary, recommendation].filter(Boolean).join('\n\n') || content;
  }
}

function normalizeMarkdownContent(content: string) {
  return normalizeLooseMarkdownTables(
    normalizeLooseBoldHeadings(
      normalizeLooseChineseHeadings(
        normalizeLooseNumberedHeadings(
          unwrapLooseAnswerContent(content)
        .replace(/\r\n/g, '\n')
        .replace(/\u00a0/g, ' ')
        .replace(/[\u200b-\u200d\ufeff]/g, '')
        .replace(/｜/g, '|')
        .replace(/\\\|/g, '|')
        .replace(/\\n/g, '\n'),
        ),
      ),
    ),
  )
    .replace(/[ \t]+(?=#{1,6}\s)/g, '\n\n')
    .replace(/[ \t]+(?=\d+\.\s+[^。\n]{2,80}(?::|：|$))/g, '\n\n');
}

function normalizeTodoListTables(content: string) {
  const lines = content.split('\n');
  const output: string[] = [];
  let rows: string[] = [];
  const flushRows = () => {
    if (!rows.length) return;
    output.push('| 时间 | 事项 |', '| --- | --- |', ...rows, '');
    rows = [];
  };
  for (const line of lines) {
    const match = /^[-*]\s+(\d{1,2}\.\d{1,2}[^|]*)\|(.+)$/.exec(line.trim());
    if (match) {
      rows.push(`| ${match[1].trim()} | ${match[2].trim()} |`);
    } else if (line.trim() || !rows.length) {
      flushRows();
      output.push(line);
    }
  }
  flushRows();
  return output.join('\n');
}

function MarkdownContent({ content }: { content: string }) {
  const lines = normalizeTodoListTables(normalizeMarkdownContent(content)).split('\n');
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let orderedList = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(
      <p key={`paragraph-${blocks.length}`}>
        {paragraph.map((line, index) => (
          <span key={index}>
            {index ? <br /> : null}
            {renderInlineMarkdown(line)}
          </span>
        ))}
      </p>,
    );
    paragraph = [];
  };

  const flushList = () => {
    if (!list.length) return;
    const List = orderedList ? 'ol' : 'ul';
    blocks.push(
      <List key={`list-${blocks.length}`}>
        {list.map((item, index) => (
          <li key={index}>{renderInlineMarkdown(item)}</li>
        ))}
      </List>,
    );
    list = [];
  };

  const pushTable = (headers: string[], rows: string[][]) => {
    const paddedHeaders = tableHeadersForRows(headers, rows);
    const groupByDate = paddedHeaders.length === 2
      && /^(时间|日期)$/.test(paddedHeaders[0].replace(/\*/g, '').trim())
      && paddedHeaders[1].replace(/\*/g, '').trim() === '事项';
    const dateGroups = new Map<string, string[]>();
    if (groupByDate) {
      for (const row of rows) {
        const date = (row[0] ?? '').trim();
        const items = dateGroups.get(date) ?? [];
        items.push(row[1] ?? '');
        dateGroups.set(date, items);
      }
    }
    blocks.push(
      <div className="desktop-markdown-table-wrap" key={`table-${blocks.length}`}>
        <table className="desktop-markdown-table">
          <thead>
            <tr>
              {paddedHeaders.map((cell, cellIndex) => (
                <th key={cellIndex}>{renderInlineMarkdown(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groupByDate ? Array.from(dateGroups, ([date, items]) => (
              <tr key={date}>
                <td>{renderInlineMarkdown(date)}</td>
                <td>
                  {items.length > 1 ? (
                    <ul>{items.map((item, index) => <li key={index}>{renderInlineMarkdown(item)}</li>)}</ul>
                  ) : renderInlineMarkdown(items[0])}
                </td>
              </tr>
            )) : rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {paddedHeaders.map((_, cellIndex) => (
                  <td key={cellIndex}>{renderInlineMarkdown(row[cellIndex] ?? '')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    let nextNonEmptyLineIndex = index + 1;
    while (nextNonEmptyLineIndex < lines.length && !lines[nextNonEmptyLineIndex].trim()) {
      nextNonEmptyLineIndex += 1;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    const numberedHeading = line.match(/^\s*\*\*((?:\d+[.)]\s+).{2,80})\*\*\s*$/);
    const chineseHeading = line.match(/^\s*([一二三四五六七八九十]+[、.．]\s*[^。！？；，、,.!?\n]{2,32})\s*$/);
    const boldHeading = line.match(/^\s*\*\*([^*\n。！？；，、,.!?]{2,24})\*\*\s*$/);
    const quote = line.match(/^\s*>\s?(.+)$/);
    const tableCandidate = isTableRow(line) && nextNonEmptyLineIndex < lines.length && isTableDivider(lines[nextNonEmptyLineIndex]);

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    if (line.trim() === '|') {
      flushParagraph();
      flushList();
      continue;
    }

    if (heading) {
      flushParagraph();
      flushList();
      const Heading = `h${heading[1].length}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      blocks.push(<Heading key={`heading-${blocks.length}`}>{renderInlineMarkdown(heading[2])}</Heading>);
      continue;
    }

    if (numberedHeading) {
      flushParagraph();
      flushList();
      blocks.push(<h3 key={`numbered-heading-${blocks.length}`}>{renderInlineMarkdown(numberedHeading[1])}</h3>);
      continue;
    }

    if (chineseHeading) {
      flushParagraph();
      flushList();
      blocks.push(<h3 key={`chinese-heading-${blocks.length}`}>{renderInlineMarkdown(chineseHeading[1])}</h3>);
      continue;
    }

    if (boldHeading) {
      flushParagraph();
      flushList();
      blocks.push(<h3 key={`bold-heading-${blocks.length}`}>{renderInlineMarkdown(boldHeading[1])}</h3>);
      continue;
    }

    if (unordered || ordered) {
      flushParagraph();
      if (list.length && orderedList !== Boolean(ordered)) flushList();
      orderedList = Boolean(ordered);
      list.push((unordered ?? ordered)![1]);
      continue;
    }

    if (quote) {
      flushParagraph();
      flushList();
      blocks.push(<blockquote key={`quote-${blocks.length}`}>{renderInlineMarkdown(quote[1])}</blockquote>);
      continue;
    }

    if (tableCandidate) {
      flushParagraph();
      flushList();
      const headers = splitTableRow(line);
      const rows: string[][] = [];
      index = nextNonEmptyLineIndex + 1;
      while (index < lines.length) {
        if (!lines[index].trim()) {
          let nextTableLineIndex = index + 1;
          while (nextTableLineIndex < lines.length && !lines[nextTableLineIndex].trim()) {
            nextTableLineIndex += 1;
          }
          if (nextTableLineIndex < lines.length && isTableRow(lines[nextTableLineIndex])) {
            index = nextTableLineIndex;
          } else {
            break;
          }
        }
        if (!isTableRow(lines[index])) break;
        if (isTableDivider(lines[index])) {
          index += 1;
          continue;
        }
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      index -= 1;
      pushTable(headers, rows);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  return <div className="desktop-markdown">{blocks}</div>;
}

function iconForMemory(type: MemoryType) {
  if (type === '方法型') return { icon: cardFolderIcon, tint: 'cyan' as const };
  return { icon: cardNoteIcon, tint: 'indigo' as const };
}

function iconForFolderName(name: string) {
  const folderIcons: Record<string, string> = {
    客户案例: usersIcon,
    会议纪要: presentationIcon,
    日常待办: calendarCheckIcon,
    知识沉淀: bookOpenIcon,
  };
  return folderIcons[name] ?? folderClosedIcon;
}

function iconForChatMemory(memory: MemoryCard, folders: MemoryFolder[]) {
  const folder = folders.find((item) => item.id === memory.folder_id);
  return folder ? iconForFolderName(folder.name) : iconForMemory(memory.type).icon;
}

function truncateMemoryTitle(title: string) {
  const characters = Array.from(title);
  return characters.length > 15 ? `${characters.slice(0, 15).join('')}...` : title;
}

function resizeComposerTextarea(event: React.FormEvent<HTMLTextAreaElement>) {
  const textarea = event.currentTarget;
  textarea.style.height = 'auto';
  textarea.style.height = `${textarea.scrollHeight}px`;
}

const HOME_MEMORY_GROUPS: Array<{
  id: string;
  title: string;
  description?: string;
  accent: string;
  background: string;
  textColor: string;
  arrowColor: string;
  icon: string;
}> = [
  {
    id: 'folder-customer-cases',
    title: '客户案例',
    description: '客户反馈、试用与业务现场',
    accent: 'blue',
    background: 'linear-gradient(160deg, #e2edff, #f5f9ff)',
    textColor: 'rgba(0, 0, 0, 0.9)',
    arrowColor: '#dfeaff',
    icon: homeIconCases,
  },
  {
    id: 'folder-daily-todos',
    title: '日常待办',
    description: '需要推进、跟进和完成的事项',
    accent: 'purple',
    background: 'linear-gradient(160deg, #fff2df, #fff9ef)',
    textColor: 'rgba(0, 0, 0, 0.9)',
    arrowColor: '#ffe7cc',
    icon: homeIconTodos,
  },
  {
    id: 'folder-meetings',
    title: '会议纪要',
    description: '会议结论、评审与同步记录',
    accent: 'orange',
    background: 'linear-gradient(160deg, #eceaff, #f8f7ff)',
    textColor: 'rgba(0, 0, 0, 0.9)',
    arrowColor: '#eae6ff',
    icon: homeIconMeetings,
  },
  {
    id: 'folder-knowledge',
    title: '知识沉淀',
    description: '经验、方法与可复用的判断',
    accent: 'cyan',
    background: 'linear-gradient(160deg, #d6f1ff, #f4fbff)',
    textColor: 'rgba(0, 0, 0, 0.9)',
    arrowColor: '#e0f4fc',
    icon: homeIconKnowledge,
  },
];

function memoryMatchesHomeGroup(memory: MemoryCard, groupId: HomeMemoryGroup): boolean {
  return memory.folder_id === groupId;
}

function homeGroupMeta(folder: MemoryFolder) {
  return HOME_MEMORY_GROUPS.find((group) => group.id === folder.id || group.title === folder.name) ?? {
    id: folder.id,
    title: folder.name,
    accent: 'generic',
    background: 'linear-gradient(160deg, #e4faf5, #f2fbf8)',
    textColor: 'rgba(0, 0, 0, 0.9)',
    arrowColor: '#dcf3eb',
    icon: homeIconCustom,
  };
}

function memoryFolderMeta(folder: MemoryFolder) {
  const fixedFolderMeta: Record<string, { icon: string; accent: 'blue' | 'orange' | 'red' | 'green' }> = {
    客户案例: { icon: memoryFolderCasesIcon, accent: 'blue' },
    会议纪要: { icon: memoryFolderMeetingsIcon, accent: 'orange' },
    日常待办: { icon: memoryFolderTodosIcon, accent: 'red' },
    知识沉淀: { icon: memoryFolderKnowledgeIcon, accent: 'green' },
  };
  return fixedFolderMeta[folder.name] ?? { icon: memoryFolderCustomIcon, accent: 'blue' as const };
}

function memoryCardFolderMeta(memory: MemoryCard, folders: MemoryFolder[]) {
  const folder = folders.find((item) => item.id === memory.folder_id);
  return folder ? memoryFolderMeta(folder) : { icon: memoryFolderCustomIcon, accent: 'blue' as const };
}

function safeSessions(raw: string | null) {
  if (!raw) return [] as ChatSession[];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      .map((item, index) => {
        const messages = Array.isArray(item.messages)
          ? item.messages.map((message, messageIndex) => safeChatMessage(message, messageIndex)).filter(
            (message): message is ChatMessage => Boolean(message),
          )
          : [];
        const timestamp = nowIso();
        return {
          id: typeof item.id === 'string' ? item.id : `legacy-session-${index}`,
          title: typeof item.title === 'string' ? item.title : '未命名对话',
          createdAt: typeof item.createdAt === 'string' ? item.createdAt : timestamp,
          updatedAt: typeof item.updatedAt === 'string' ? item.updatedAt : timestamp,
          citationCount: typeof item.citationCount === 'number' ? item.citationCount : 0,
          citedMemoryIds: Array.isArray(item.citedMemoryIds)
            ? item.citedMemoryIds.filter((memoryId): memoryId is string => typeof memoryId === 'string')
            : [],
          manualMemoryIds: Array.isArray(item.manualMemoryIds)
            ? item.manualMemoryIds.filter((memoryId): memoryId is string => typeof memoryId === 'string')
            : [],
          lastSummary: typeof item.lastSummary === 'string' ? item.lastSummary : '',
          lastRecommendation: typeof item.lastRecommendation === 'string' ? item.lastRecommendation : '',
          messages,
          lastResult: item.lastResult && typeof item.lastResult === 'object'
            ? item.lastResult as ChatResult
            : null,
        };
      });
  } catch {
    return [];
  }
}

function userStorageKey(baseKey: string, userId?: string) {
  return userId ? `${baseKey}.${userId}` : baseKey;
}

function safeHomeFolderIds(raw: string | null) {
  if (!raw) return [] as string[];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const storedIds = parsed.filter((id): id is string => typeof id === 'string');
    return [...new Set(storedIds)].slice(0, Math.max(MIN_HOME_FOLDERS, MAX_HOME_FOLDERS));
  } catch {
    return [];
  }
}

function safeAuthSession(raw: string | null): AuthSession | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<AuthSession>;
    if (
      typeof parsed.token !== 'string'
      || !parsed.user
      || typeof parsed.user !== 'object'
      || typeof parsed.user.id !== 'string'
      || typeof parsed.user.account !== 'string'
    ) {
      return null;
    }
    const user = parsed.user as AuthUser;
    return {
      token: parsed.token,
      user: {
        id: user.id,
        account: user.account,
        display_name: typeof user.display_name === 'string' ? user.display_name : user.account,
        created_at: typeof user.created_at === 'string' ? user.created_at : '',
      },
    };
  } catch {
    return null;
  }
}

export default function App() {
  const isTauriRuntime = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  const isQuickAddWindow = typeof window !== 'undefined' && window.__MEMORY_MASTER_QUICK_ADD__ === true;
  const initialAuthSession = safeAuthSession(localStorage.getItem(AUTH_STORAGE_KEY));
  const [authSession, setAuthSession] = useState<AuthSession | null>(initialAuthSession);
  const [checkingAuth, setCheckingAuth] = useState(Boolean(initialAuthSession?.token));
  const [authMode, setAuthMode] = useState<AuthMode>('login');
  const [authAccount, setAuthAccount] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authDisplayName, setAuthDisplayName] = useState('');
  const [authError, setAuthError] = useState('');
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const userChatStorageKey = userStorageKey(CHAT_STORAGE_KEY, authSession?.user.id);
  const userHomeFolderStorageKey = userStorageKey(HOME_FOLDER_STORAGE_KEY, authSession?.user.id);
  const [view, setView] = useState<View>('home');
  const [prompt, setPrompt] = useState('');
  const [chatInput, setChatInput] = useState('');
  const [memories, setMemories] = useState<MemoryCard[]>([]);
  const [memoryFolders, setMemoryFolders] = useState<MemoryFolder[]>([]);
  const [homeFolderIds, setHomeFolderIds] = useState<string[]>(() =>
    safeHomeFolderIds(localStorage.getItem(userHomeFolderStorageKey)),
  );
  const [memoryQuery, setMemoryQuery] = useState('');
  const [memoryFolderFilter, setMemoryFolderFilter] = useState<string | null>(null);
  const [memoryKeywordFilter, setMemoryKeywordFilter] = useState('');
  const [memorySourceFilter, setMemorySourceFilter] = useState('');
  const [selectedMemoryId, setSelectedMemoryId] = useState<string | null>(null);
  const [manualMemoryIds, setManualMemoryIds] = useState<string[]>([]);
  const [memoryLimitNotice, setMemoryLimitNotice] = useState(0);
  const [chatSessions, setChatSessions] = useState<ChatSession[]>(() => safeSessions(localStorage.getItem(userChatStorageKey)));
  const [activeChatSessionId, setActiveChatSessionId] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isConfigModalOpen, setIsConfigModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<ModalMode>('add');
  const [modalTitle, setModalTitle] = useState('');
  const [modalContent, setModalContent] = useState('');
  const [modalFolderId, setModalFolderId] = useState('');
  const [modalKeywords, setModalKeywords] = useState<string[]>([]);
  const [modalKeywordInput, setModalKeywordInput] = useState('');
  const [isModalKeywordInputOpen, setIsModalKeywordInputOpen] = useState(false);
  const [memoryDraftError, setMemoryDraftError] = useState<'title' | 'content' | 'folder' | ''>('');
  const [folderModalMode, setFolderModalMode] = useState<FolderModalMode | null>(null);
  const [folderModalName, setFolderModalName] = useState('');
  const [folderModalTarget, setFolderModalTarget] = useState<MemoryFolder | null>(null);
  const [folderModalError, setFolderModalError] = useState('');
  const [savingFolder, setSavingFolder] = useState(false);
  const [parsingMemory, setParsingMemory] = useState(false);
  const [config, setConfig] = useState<ApiConfig>(emptyConfig);
  const [configTab, setConfigTab] = useState<ConfigTab>('chat');
  const [loadingMemories, setLoadingMemories] = useState(true);
  const [sendingChat, setSendingChat] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [hasStoredChatApiKey, setHasStoredChatApiKey] = useState(false);
  const [hasStoredEmbeddingApiKey, setHasStoredEmbeddingApiKey] = useState(false);
  const [savingConfig, setSavingConfig] = useState(false);
  const [testingConfig, setTestingConfig] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [statusMessage, setStatusMessage] = useState('');
  const [memoryPickerTarget, setMemoryPickerTarget] = useState<MemoryPickerTarget | null>(null);
  const [memoryPickerQuery, setMemoryPickerQuery] = useState('');
  const [memoryPickerFolderId, setMemoryPickerFolderId] = useState<string | null>(null);
  const [memoryPickerSelectedIds, setMemoryPickerSelectedIds] = useState<string[]>([]);
  const [isHistoryDrawerOpen, setIsHistoryDrawerOpen] = useState(true);
  const [isSettingsMenuOpen, setIsSettingsMenuOpen] = useState(false);
  const [historySearchQuery, setHistorySearchQuery] = useState('');
  const [chatContextMenu, setChatContextMenu] = useState<ChatContextMenu | null>(null);
  const [memoryFolderContextMenu, setMemoryFolderContextMenu] = useState<MemoryFolderContextMenu | null>(null);
  const [chatRenameSessionId, setChatRenameSessionId] = useState<string | null>(null);
  const [chatRenameTitle, setChatRenameTitle] = useState('');
  const [copiedAssistantMessageId, setCopiedAssistantMessageId] = useState<string | null>(null);
  const [collapsedRecallMessageIds, setCollapsedRecallMessageIds] = useState<string[]>([]);
  const [homeMemoryGroup, setHomeMemoryGroup] = useState<HomeMemoryGroup | null>(null);
  const [homeMemoryPage, setHomeMemoryPage] = useState(0);
  const [homeCardScale, setHomeCardScale] = useState(1);
  const [pendingDeleteMemory, setPendingDeleteMemory] = useState<MemoryCard | null>(null);
  const [pendingDeleteFolder, setPendingDeleteFolder] = useState<MemoryFolder | null>(null);
  const [deletingMemory, setDeletingMemory] = useState(false);
  const [memoryUploadToast, setMemoryUploadToast] = useState<MemoryUploadToast | null>(null);
  const homeOverviewRef = useRef<HTMLDivElement | null>(null);
  const homeCardStageRef = useRef<HTMLDivElement | null>(null);
  const homeComposerRef = useRef<HTMLDivElement | null>(null);
  const memoryUploadToastTimerRef = useRef<number | null>(null);
  const activeChatRequestRef = useRef<string | null>(null);
  const interruptedChatRequestIdsRef = useRef<Set<string>>(new Set());

  const memoryKeywords = [...new Set(memories.flatMap((memory) => memory.keywords))].sort((left, right) => left.localeCompare(right, 'zh-CN'));
  const memorySources = [...new Set(memories.map((memory) => memory.source))].sort((left, right) => left.localeCompare(right, 'zh-CN'));
  const filteredMemories = memories.filter((memory) => {
    const keyword = memoryQuery.trim().toLowerCase();
    const keywordMatch = !keyword || (
      memory.title.toLowerCase().includes(keyword) ||
      memory.content.toLowerCase().includes(keyword) ||
      memory.keywords.join(' ').toLowerCase().includes(keyword)
    );
    return keywordMatch
      && (!memoryFolderFilter || memory.folder_id === memoryFolderFilter)
      && (!memoryKeywordFilter || memory.keywords.includes(memoryKeywordFilter))
      && (!memorySourceFilter || memory.source === memorySourceFilter);
  });

  const sortedSessions = [...chatSessions].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  const filteredHistorySessions = sortedSessions.filter((session) => {
    const keyword = historySearchQuery.trim().toLowerCase();
    if (!keyword) return true;
    return [session.title, sessionPreview(session), ...session.messages.map((message) => message.text)]
      .join(' ')
      .toLowerCase()
      .includes(keyword);
  });
  const activeChatSession = chatSessions.find((session) => session.id === activeChatSessionId) ?? null;
  const isChatView = view === 'chat' || view === 'history';
  const canShowHistoryDrawer = view === 'home' || isChatView;
  const selectedMemory = memories.find((memory) => memory.id === selectedMemoryId) ?? filteredMemories[0] ?? memories[0] ?? null;
  const latestAssistantText =
    activeChatSession?.messages.filter((message) => message.role === 'assistant' && !message.failed).at(-1)?.text ?? '';
  const modalStatusTag = modalMode === 'save' ? '对话沉淀' : '手动添加';
  const selectedHomeMemories = manualMemoryIds
    .map((memoryId) => memories.find((memory) => memory.id === memoryId))
    .filter((memory): memory is MemoryCard => Boolean(memory));
  const activeHomeFolder = memoryFolders.find((folder) => folder.id === homeMemoryGroup) ?? null;
  const activeHomeMemoryGroup = activeHomeFolder ? homeGroupMeta(activeHomeFolder) : null;
  const homeDisplayFolders = homeFolderIds
    .map((folderId) => memoryFolders.find((folder) => folder.id === folderId))
    .filter((folder): folder is MemoryFolder => Boolean(folder))
    .sort((left, right) => {
      const leftIndex = DEFAULT_HOME_FOLDER_NAMES.findIndex((name) => name === left.name);
      const rightIndex = DEFAULT_HOME_FOLDER_NAMES.findIndex((name) => name === right.name);
      if (leftIndex === -1 && rightIndex === -1) return 0;
      if (leftIndex === -1) return 1;
      if (rightIndex === -1) return -1;
      return leftIndex - rightIndex;
    })
    .slice(0, MAX_HOME_FOLDERS);
  const homeCardLayoutWidth = 908;
  const homeGroupMemories = homeMemoryGroup
    ? memories
      .filter((memory) => memoryMatchesHomeGroup(memory, homeMemoryGroup))
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
      .slice(0, 18)
    : [];
  const homeMemoryPageCount = Math.max(1, Math.ceil(homeGroupMemories.length / 6));
  const visibleHomeGroupMemories = homeGroupMemories.slice(homeMemoryPage * 6, homeMemoryPage * 6 + 6);
  const memoryPickerMemories = memories.filter((memory) => {
    const keyword = memoryPickerQuery.trim().toLowerCase();
    const matchesKeyword = !keyword
      || memory.title.toLowerCase().includes(keyword)
      || memory.content.toLowerCase().includes(keyword);
    return matchesKeyword && (!memoryPickerFolderId || memory.folder_id === memoryPickerFolderId);
  });

  function showMemoryUploadToast(memory: MemoryCard) {
    const folder = memoryFolders.find((item) => item.id === memory.folder_id);
    const folderMeta = memoryCardFolderMeta(memory, memoryFolders);
    setMemoryUploadToast({
      title: memory.title,
      folderName: folder?.name ?? '未分组',
      icon: folderMeta.icon,
      accent: folderMeta.accent,
    });
    if (memoryUploadToastTimerRef.current) {
      window.clearTimeout(memoryUploadToastTimerRef.current);
    }
    memoryUploadToastTimerRef.current = window.setTimeout(() => {
      setMemoryUploadToast(null);
      memoryUploadToastTimerRef.current = null;
    }, 3000);
  }

  useEffect(() => {
    if (!authSession) {
      localStorage.removeItem(AUTH_STORAGE_KEY);
      return;
    }
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(authSession));
  }, [authSession]);

  useEffect(() => {
    const token = initialAuthSession?.token;
    if (!token) {
      setCheckingAuth(false);
      return;
    }

    let active = true;
    api.currentUser(token)
      .then((response) => {
        if (!active) return;
        setAuthSession({ token, user: response.user });
      })
      .catch(() => {
        if (!active) return;
        setAuthSession(null);
      })
      .finally(() => {
        if (active) setCheckingAuth(false);
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!authSession) return;
    localStorage.setItem(userChatStorageKey, JSON.stringify(chatSessions));
  }, [authSession, chatSessions, userChatStorageKey]);

  useEffect(() => {
    if (!authSession) return;
    localStorage.setItem(userHomeFolderStorageKey, JSON.stringify(homeFolderIds));
  }, [authSession, homeFolderIds, userHomeFolderStorageKey]);

  useEffect(() => {
    if (!authSession) return;
    setChatSessions(safeSessions(localStorage.getItem(userChatStorageKey)));
    setHomeFolderIds(safeHomeFolderIds(localStorage.getItem(userHomeFolderStorageKey)));
    setActiveChatSessionId(null);
    setManualMemoryIds([]);
    setSelectedMemoryId(null);
  }, [authSession?.user.id]);

  useEffect(() => {
    function closeChatContextMenu(event: PointerEvent) {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-chat-context-menu]') && !target.closest('[data-memory-folder-context-menu]')) {
        setChatContextMenu(null);
        setMemoryFolderContextMenu(null);
      }
      if (!target.closest('[data-settings-menu]')) {
        setIsSettingsMenuOpen(false);
      }
    }

    document.addEventListener('pointerdown', closeChatContextMenu);
    return () => document.removeEventListener('pointerdown', closeChatContextMenu);
  }, []);

  useEffect(() => {
    if (!authSession) return;
    void refreshMemories();
    void refreshMemoryFolders();
  }, [authSession]);

  useEffect(() => {
    if (isQuickAddWindow || !isTauriRuntime) return;
    let unlisten: (() => void) | undefined;
    const listenPromise = listen<MemoryCard>('memory-created', (event) => {
      void refreshMemories();
      showMemoryUploadToast(event.payload);
      setErrorMessage('');
    }).then((cleanup) => {
      unlisten = cleanup;
    });
    return () => {
      void listenPromise.then(() => unlisten?.());
    };
  }, [isQuickAddWindow, isTauriRuntime]);

  useEffect(() => {
    if (!selectedMemoryId && memories[0]) setSelectedMemoryId(memories[0].id);
  }, [memories, selectedMemoryId]);

  useEffect(() => {
    if (!memoryFolders.length) return;
    setHomeFolderIds((current) => {
      const availableIds = new Set(memoryFolders.map((folder) => folder.id));
      const availableDefaults = DEFAULT_HOME_FOLDER_NAMES
        .map((name) => memoryFolders.find((folder) => folder.name === name)?.id)
        .filter((id): id is string => Boolean(id));
      const next = [...new Set([
        ...current.filter((id) => availableIds.has(id)),
        ...availableDefaults,
      ])].slice(0, MAX_HOME_FOLDERS);
      return next.length === current.length && next.every((id, index) => id === current[index]) ? current : next;
    });
  }, [memoryFolders]);

  useEffect(() => {
    if (view !== 'home' || homeMemoryGroup) return;
    const overview = homeOverviewRef.current;
    const cardStage = homeCardStageRef.current;
    const composer = homeComposerRef.current;
    if (!overview || !cardStage || !composer) return;

    const updateHomeCardScale = () => {
      const widthScale = overview.clientWidth / homeCardLayoutWidth;
      const availableHeight = overview.clientHeight - cardStage.offsetTop - composer.offsetHeight;
      if (widthScale <= 0 || overview.clientHeight <= 0) return;
      const heightScale = availableHeight > 0 ? availableHeight / 300 : 1;
      const nextScale = Math.min(1, widthScale, heightScale);
      if (!Number.isFinite(nextScale) || nextScale <= 0) return;
      const stableScale = Number(nextScale.toFixed(3));
      setHomeCardScale(stableScale);
    };

    updateHomeCardScale();
    const frameId = window.requestAnimationFrame(updateHomeCardScale);
    const resizeObserver = new ResizeObserver(updateHomeCardScale);
    resizeObserver.observe(overview);
    window.addEventListener('resize', updateHomeCardScale);
    return () => {
      window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      window.removeEventListener('resize', updateHomeCardScale);
    };
  }, [view, homeMemoryGroup, homeCardLayoutWidth]);

  useEffect(() => {
    if (isQuickAddWindow || isTauriRuntime) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || !event.shiftKey || event.altKey || event.key.toLowerCase() !== 'm') return;
      event.preventDefault();
      if (isTauriRuntime) {
        void invoke('show_quick_add_window');
      } else {
        openModal('add');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isQuickAddWindow, isTauriRuntime]);

  useEffect(() => {
    if (!isQuickAddWindow) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      void handleCloseQuickAdd();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isQuickAddWindow]);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    document.body.classList.toggle('is-quick-add-window', isQuickAddWindow);
    return () => {
      document.body.classList.remove('is-quick-add-window');
    };
  }, [isQuickAddWindow]);

  useEffect(() => {
    if (!isQuickAddWindow || !isTauriRuntime) return;
    const subscription = getCurrentWindow().onFocusChanged(({ payload }) => {
      if (!payload) return;
      document.querySelector<HTMLTextAreaElement>('.quick-add-card__content')?.focus();
      void refreshMemoryFolders();
    });
    return () => { void subscription.then(stop => stop()); };
  }, [isQuickAddWindow, isTauriRuntime]);

  async function refreshMemories() {
    setLoadingMemories(true);
    try {
      const response = await api.listMemories();
      setMemories(response.items);
      setErrorMessage('');
      if (!selectedMemoryId && response.items[0]) setSelectedMemoryId(response.items[0].id);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '记忆卡加载失败');
    } finally {
      setLoadingMemories(false);
    }
  }

  async function refreshMemoryFolders() {
    try {
      const response = await api.listFolders();
      setMemoryFolders(response.items);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分组加载失败');
    }
  }

  function openCreateFolderModal() {
    setFolderModalMode('create');
    setFolderModalName('');
    setFolderModalTarget(null);
    setFolderModalError('');
    setErrorMessage('');
  }

  function openRenameFolderModal(folder: MemoryFolder) {
    setFolderModalMode('rename');
    setFolderModalName(folder.name);
    setFolderModalTarget(folder);
    setFolderModalError('');
    setErrorMessage('');
  }

  function closeFolderModal() {
    if (savingFolder) return;
    setFolderModalMode(null);
    setFolderModalName('');
    setFolderModalTarget(null);
    setFolderModalError('');
  }

  async function handleFolderSubmit(event: FormEvent) {
    event.preventDefault();
    const name = folderModalName.trim();
    if (!name || savingFolder) return;
    const duplicateFolder = memoryFolders.some(
      (folder) => folder.name.trim() === name && folder.id !== folderModalTarget?.id,
    );
    if (duplicateFolder) {
      setFolderModalError('分组名称已存在，请重新命名');
      return;
    }
    setSavingFolder(true);
    try {
      if (folderModalMode === 'rename' && folderModalTarget) {
        await api.updateFolder(folderModalTarget.id, name);
        setStatusMessage('分组已重命名');
      } else {
        const folder = await api.createFolder(name);
        setMemoryFolderFilter(folder.id);
        setStatusMessage('分组已创建');
      }
      await refreshMemoryFolders();
      setErrorMessage('');
      setFolderModalError('');
      setFolderModalMode(null);
      setFolderModalName('');
      setFolderModalTarget(null);
    } catch (error) {
      setFolderModalError(error instanceof Error ? error.message : '分组保存失败');
      setStatusMessage('');
    } finally {
      setSavingFolder(false);
    }
  }

  async function removeMemoryFolder(folder: MemoryFolder) {
    if (savingFolder) return;
    setSavingFolder(true);
    try {
      await api.deleteFolder(folder.id);
      await Promise.all([refreshMemoryFolders(), refreshMemories()]);
      if (memoryFolderFilter === folder.id) setMemoryFolderFilter(null);
      if (homeMemoryGroup === folder.id) {
        setHomeMemoryGroup(null);
        setHomeMemoryPage(0);
      }
      setHomeFolderIds((current) => current.filter((id) => id !== folder.id));
      setStatusMessage('分组已删除');
      setErrorMessage('');
      setPendingDeleteFolder(null);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分组删除失败');
      setStatusMessage('');
    } finally {
      setSavingFolder(false);
    }
  }

  function closeFolderDeleteConfirm() {
    if (savingFolder) return;
    setPendingDeleteFolder(null);
  }

  async function handleAuthSubmit(event: FormEvent) {
    event.preventDefault();
    const account = authAccount.trim();
    const password = authPassword;
    if (!account || !password || authSubmitting) return;
    setAuthSubmitting(true);
    setAuthError('');
    try {
      const session = authMode === 'register'
        ? await api.register(account, password, authDisplayName.trim())
        : await api.login(account, password);
      setAuthSession(session);
      setAuthPassword('');
      setAuthDisplayName('');
      setErrorMessage('');
      setStatusMessage('');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : '登录失败，请重试');
    } finally {
      setAuthSubmitting(false);
    }
  }

  async function handleLogout() {
    const token = authSession?.token;
    setAuthSession(null);
    setAuthPassword('');
    setAuthError('');
    setView('home');
    setMemories([]);
    setMemoryFolders([]);
    setChatSessions([]);
    setHomeFolderIds([]);
    setSelectedMemoryId(null);
    setManualMemoryIds([]);
    setActiveChatSessionId(null);
    setIsHistoryDrawerOpen(false);
    setIsSettingsMenuOpen(false);
    setIsConfigModalOpen(false);
    if (!token) return;
    try {
      await api.logout(token);
    } catch {
      // Local logout should succeed even if the backend session is already gone.
    }
  }

  function toggleHomeFolder(folderId: string) {
    if (homeFolderIds.includes(folderId)) {
      if (homeFolderIds.length <= MIN_HOME_FOLDERS) {
        setErrorMessage('首页至少展示 1 个分组');
        setStatusMessage('');
        return;
      }
      setHomeFolderIds((current) => current.filter((id) => id !== folderId));
      return;
    }
    if (homeFolderIds.length >= MAX_HOME_FOLDERS) {
      setErrorMessage('首页最多展示 4 个分组');
      setStatusMessage('');
      return;
    }
    setHomeFolderIds((current) => [...current, folderId]);
    setErrorMessage('');
  }

  async function loadConfig() {
    setLoadingConfig(true);
    setConfigLoaded(false);
    try {
      const response = await api.getConfig();
      setConfig(response);
      setHasStoredChatApiKey(response.api_key === MASKED_CONFIG_SECRET);
      setHasStoredEmbeddingApiKey(response.embedding_api_key === MASKED_CONFIG_SECRET);
      setConfigLoaded(true);
      setErrorMessage('');
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '配置加载失败');
    } finally {
      setLoadingConfig(false);
    }
  }

  function openConfigModal() {
    setConfigTab('chat');
    setConfig(emptyConfig);
    setHasStoredChatApiKey(false);
    setHasStoredEmbeddingApiKey(false);
    setConfigLoaded(false);
    setErrorMessage('');
    setStatusMessage('');
    setIsSettingsMenuOpen(false);
    setIsConfigModalOpen(true);
    void loadConfig();
  }

  function handleCloseConfigModal() {
    if (savingConfig || testingConfig) return;
    setIsConfigModalOpen(false);
    setErrorMessage('');
    setStatusMessage('');
  }

  function openView(nextView: View) {
    setView(nextView);
    setErrorMessage('');
    setStatusMessage('');
    if (nextView === 'chat' || nextView === 'history') {
      setIsHistoryDrawerOpen(true);
    } else if (nextView !== 'home') {
      setIsHistoryDrawerOpen(false);
    }
    setMemoryPickerTarget(null);
    setIsSettingsMenuOpen(false);
    if (nextView === 'home') {
      setPrompt('');
      setChatInput('');
      setManualMemoryIds([]);
      setActiveChatSessionId(null);
      setHomeMemoryGroup(null);
      setHomeMemoryPage(0);
    }
    if (nextView === 'history') {
      setPrompt('');
      setChatInput('');
      setManualMemoryIds([]);
      setActiveChatSessionId(sortedSessions[0]?.id ?? null);
    }
  }

  function openLatestHistorySession() {
    setView('chat');
    setIsHistoryDrawerOpen(true);
    setPrompt('');
    setChatInput('');
    setManualMemoryIds([]);
    setMemoryPickerTarget(null);
    setErrorMessage('');
    setStatusMessage('');
    setActiveChatSessionId(sortedSessions[0]?.id ?? null);
  }

  async function handleWindowAction(action: 'close' | 'minimize' | 'maximize') {
    if (!isTauriRuntime) return;
    try {
      const window = getCurrentWindow();
      if (action === 'close') await window.hide();
      if (action === 'minimize') await window.minimize();
      if (action === 'maximize') await window.toggleMaximize();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '窗口操作失败');
    }
  }

  function handleWindowDrag(event: React.MouseEvent<HTMLDivElement>) {
    if (!isTauriRuntime || event.button !== 0) return;

    const homepage = event.currentTarget;
    const { top } = homepage.getBoundingClientRect();
    if (event.clientY - top >= 64) return;

    const target = event.target as HTMLElement;
    if (target.closest('button, input, textarea, select, a, [data-tauri-drag-region="false"]')) return;

    void getCurrentWindow().startDragging();
  }

  function startNewQuestion() {
    if (isChatView) {
      setPrompt('');
      setChatInput('');
      setManualMemoryIds([]);
      setActiveChatSessionId(null);
      setMemoryPickerTarget(null);
      setErrorMessage('');
      setStatusMessage('');
      return;
    }
    setIsHistoryDrawerOpen(false);
    openView('home');
  }

  function openChatContextMenu(event: React.MouseEvent<HTMLButtonElement>, sessionId: string) {
    event.preventDefault();
    event.stopPropagation();
    const menuWidth = 121;
    const menuHeight = 80;
    const margin = 8;
    setChatContextMenu({
      sessionId,
      x: Math.min(event.clientX, window.innerWidth - menuWidth - margin),
      y: Math.min(event.clientY, window.innerHeight - menuHeight - margin),
    });
  }

  function openMemoryFolderContextMenu(event: React.MouseEvent<HTMLDivElement>, folder: MemoryFolder) {
    event.preventDefault();
    event.stopPropagation();
    const menuWidth = folder.is_default ? 132 : 132;
    const menuHeight = folder.is_default ? 48 : 144;
    const margin = 8;
    setMemoryFolderContextMenu({
      folder,
      x: Math.min(event.clientX, window.innerWidth - menuWidth - margin),
      y: Math.min(event.clientY, window.innerHeight - menuHeight - margin),
    });
  }

  function closeMemoryFolderContextMenu() {
    setMemoryFolderContextMenu(null);
  }

  function handleMemoryFolderHomeToggle(folder: MemoryFolder) {
    closeMemoryFolderContextMenu();
    toggleHomeFolder(folder.id);
  }

  function handleMemoryFolderRename(folder: MemoryFolder) {
    closeMemoryFolderContextMenu();
    openRenameFolderModal(folder);
  }

  function handleMemoryFolderDelete(folder: MemoryFolder) {
    closeMemoryFolderContextMenu();
    setPendingDeleteFolder(folder);
  }

  function renameChatSession() {
    if (!chatContextMenu) return;
    const session = chatSessions.find((item) => item.id === chatContextMenu.sessionId);
    setChatContextMenu(null);
    if (!session) return;
    setChatRenameSessionId(session.id);
    setChatRenameTitle(session.title);
  }

  function closeChatRenameModal() {
    setChatRenameSessionId(null);
    setChatRenameTitle('');
  }

  function handleChatRenameSubmit(event: FormEvent) {
    event.preventDefault();
    const sessionId = chatRenameSessionId;
    const nextTitle = chatRenameTitle.trim();
    if (!sessionId || !nextTitle) return;
    setChatSessions((current) => current.map((item) => (
      item.id === sessionId ? { ...item, title: nextTitle } : item
    )));
    closeChatRenameModal();
  }

  function deleteChatSession() {
    if (!chatContextMenu) return;
    const sessionId = chatContextMenu.sessionId;
    setChatContextMenu(null);
    setChatSessions((current) => current.filter((item) => item.id !== sessionId));
    if (sessionId === activeChatSessionId) {
      setActiveChatSessionId(null);
      setManualMemoryIds([]);
    }
  }

  function openMemoryDetail(memoryId: string) {
    setSelectedMemoryId(memoryId);
    openView('memory-detail');
  }

  function goBackToMemories() {
    openView('memories');
  }

  function openChatSession(session: ChatSession) {
    setChatContextMenu(null);
    setActiveChatSessionId(session.id);
    setManualMemoryIds([]);
    if (view === 'history') {
      setIsHistoryDrawerOpen(true);
      setMemoryPickerTarget(null);
      setErrorMessage('');
      setStatusMessage('');
      return;
    }
    openView('chat');
  }

  useEffect(() => {
    if (!memoryLimitNotice) return;
    const timer = window.setTimeout(() => setMemoryLimitNotice(0), 3000);
    return () => window.clearTimeout(timer);
  }, [memoryLimitNotice]);

  function toggleManualMemory(memoryId: string) {
    if (!manualMemoryIds.includes(memoryId) && manualMemoryIds.length >= MAX_MANUAL_MEMORY_IDS) {
      setMemoryLimitNotice((current) => current + 1);
      return;
    }
    setMemoryLimitNotice(0);
    setManualMemoryIds((current) =>
      current.includes(memoryId)
        ? current.filter((item) => item !== memoryId)
        : current.length >= MAX_MANUAL_MEMORY_IDS
          ? current
          : [...current, memoryId],
    );
  }

  function openMemoryPicker(target: MemoryPickerTarget) {
    setMemoryPickerTarget(target);
    setMemoryPickerQuery('');
    setMemoryPickerFolderId(null);
    setMemoryPickerSelectedIds(manualMemoryIds);
  }

  function closeMemoryPicker() {
    setMemoryPickerTarget(null);
    setMemoryPickerQuery('');
    setMemoryPickerFolderId(null);
  }

  function toggleMemoryPickerSelection(memoryId: string) {
    if (!memoryPickerSelectedIds.includes(memoryId) && memoryPickerSelectedIds.length >= MAX_MANUAL_MEMORY_IDS) {
      setMemoryLimitNotice((current) => current + 1);
      return;
    }
    setMemoryLimitNotice(0);
    setMemoryPickerSelectedIds((current) => (
      current.includes(memoryId)
        ? current.filter((item) => item !== memoryId)
        : current.length >= MAX_MANUAL_MEMORY_IDS
          ? current
          : [...current, memoryId]
    ));
  }

  function confirmMemoryPicker() {
    setManualMemoryIds(memoryPickerSelectedIds);
    closeMemoryPicker();
  }

  function interruptChat() {
    const requestId = activeChatRequestRef.current;
    if (!requestId) return;

    interruptedChatRequestIdsRef.current.add(requestId);
    activeChatRequestRef.current = null;
    setSendingChat(false);
    setStatusMessage('');
  }

  function addRecentMemoryToComposer(memoryId: string) {
    if (manualMemoryIds.includes(memoryId)) return;
    if (manualMemoryIds.length >= MAX_MANUAL_MEMORY_IDS) {
      setMemoryLimitNotice((current) => current + 1);
      return;
    }
    setMemoryLimitNotice(0);
    setManualMemoryIds((current) => (
      current.includes(memoryId) || current.length >= MAX_MANUAL_MEMORY_IDS
        ? current
        : [...current, memoryId]
    ));
  }

  function buildNewSession(query: string): ChatSession {
    const timestamp = nowIso();
    return {
      id: `session-${Date.now()}`,
      title: sessionTitle(query),
      createdAt: timestamp,
      updatedAt: timestamp,
      citationCount: 0,
      citedMemoryIds: [],
      manualMemoryIds: [],
      lastSummary: '',
      lastRecommendation: '',
      messages: [],
      lastResult: null,
    };
  }

  async function sendChat(queryText?: string, memoryIds?: string[], forceNewSession = false) {
    const query = (queryText ?? chatInput).trim();
    if (!query || sendingChat) return;

    const requestId = `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const manualIds = MEMORY_RECALL_ENABLED ? (memoryIds ?? manualMemoryIds) : [];
    const baseSession = forceNewSession ? buildNewSession(query) : activeChatSession ?? buildNewSession(query);
    const userMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      text: query,
      createdAt: nowIso(),
      memoryIds: manualIds,
    };
    const draftSession: ChatSession = {
      ...baseSession,
      title: baseSession.messages.length ? baseSession.title : sessionTitle(query),
      updatedAt: userMessage.createdAt,
      manualMemoryIds: manualIds,
      messages: [...baseSession.messages, userMessage],
    };

    setActiveChatSessionId(draftSession.id);
    setChatSessions((current) => {
      const exists = current.some((session) => session.id === draftSession.id);
      return exists
        ? current.map((session) => (session.id === draftSession.id ? draftSession : session))
        : [draftSession, ...current];
    });
    setChatInput('');
    setPrompt('');
    setManualMemoryIds([]);
    openView('chat');
    activeChatRequestRef.current = requestId;
    setSendingChat(true);
    setStatusMessage(CHAT_LOADING_TEXT);
    setErrorMessage('');

    try {
      const response = await api.chat(
        query,
        manualIds,
        baseSession.messages.filter((message) => !message.failed).map((message) => ({ role: message.role, text: message.text })),
        baseSession.lastResult?.meta.retrieval_scope,
      );
      if (interruptedChatRequestIdsRef.current.has(requestId)) {
        interruptedChatRequestIdsRef.current.delete(requestId);
        return;
      }
      if (activeChatRequestRef.current !== requestId) return;

      const isModelError = response.meta.generation_mode === 'model_error';
      const shouldHideRecall = isRevisionTurnType(response.meta.turn_type);
      const messageMemoryUsage = shouldHideRecall
        ? memoryUsageWithoutRecall(response.memory_usage)
        : response.memory_usage;
      const assistantMessage: ChatMessage = {
        id: `msg-${Date.now() + 1}`,
        role: 'assistant',
        text: isModelError ? modelErrorText(response) : answerText(response, query),
        failed: isModelError,
        createdAt: nowIso(),
        citationMemoryIds: messageMemoryUsage.citations.map((item) => item.id),
        autoRecalledMemoryIds: messageMemoryUsage.auto_recalled_memory_ids,
        memoryUsage: messageMemoryUsage,
      };
      const finalSession: ChatSession = {
        ...draftSession,
        updatedAt: assistantMessage.createdAt,
        citationCount: messageMemoryUsage.citation_count,
        citedMemoryIds: messageMemoryUsage.citations.map((item) => item.id),
        lastSummary: response.answer.summary,
        lastRecommendation: response.answer.recommendation,
        messages: [...draftSession.messages, assistantMessage],
        lastResult: isModelError ? null : response,
      };
      setChatSessions((current) => current.map((session) => (session.id === finalSession.id ? finalSession : session)));
      setStatusMessage(
        [
          response.meta.generation_mode === 'model'
            ? '已使用真实模型返回结果'
            : response.meta.generation_mode === 'local_fallback'
              ? '当前使用本地兜底回答'
              : '',
          ...(response.meta.warnings ?? []),
        ]
          .filter(Boolean)
          .join('；'),
      );
      setErrorMessage(
        isModelError ? response.meta.error || '模型调用失败，请检查配置后重试' : '',
      );
      void refreshMemories();
    } catch (error) {
      if (interruptedChatRequestIdsRef.current.has(requestId)) {
        interruptedChatRequestIdsRef.current.delete(requestId);
        return;
      }
      if (activeChatRequestRef.current !== requestId) return;

      const assistantMessage: ChatMessage = {
        id: `msg-${Date.now() + 1}`,
        role: 'assistant',
        text: error instanceof Error ? `调用失败：${error.message}` : '调用失败，请稍后重试',
        failed: true,
        createdAt: nowIso(),
      };
      const failedSession: ChatSession = {
        ...draftSession,
        updatedAt: assistantMessage.createdAt,
        lastSummary: assistantMessage.text,
        lastRecommendation: '',
        messages: [...draftSession.messages, assistantMessage],
        lastResult: null,
      };
      setChatSessions((current) => current.map((session) => (session.id === failedSession.id ? failedSession : session)));
      setErrorMessage(error instanceof Error ? error.message : '对话请求失败');
    } finally {
      if (activeChatRequestRef.current === requestId) {
        activeChatRequestRef.current = null;
        setSendingChat(false);
      }
    }
  }

  function handleHomeSubmit(event: FormEvent) {
    event.preventDefault();
    void sendChat(prompt, manualMemoryIds, true);
  }

  function handleChatSubmit(event: FormEvent) {
    event.preventDefault();
    void sendChat(chatInput, manualMemoryIds);
  }

  function openModal(mode: ModalMode, assistantText = latestAssistantText) {
    setModalMode(mode);
    if (mode === 'edit' && selectedMemory) {
      setModalTitle(selectedMemory.title);
      setModalContent(selectedMemory.content);
      setModalFolderId(selectedMemory.folder_id ?? '');
      setModalKeywords(selectedMemory.keywords);
    } else if (mode === 'save') {
      const capture = activeChatSession?.lastResult?.memory_capture;
      const suggested = capture?.should_save && capture.suggested_memory_card.content === assistantText
        ? capture.suggested_memory_card
        : undefined;
      setModalTitle(suggested?.title ?? sessionTitle(prompt || '对话沉淀'));
      setModalContent(suggested?.content ?? assistantText);
      setModalFolderId('');
      setModalKeywords([]);
    } else {
      setModalTitle('');
      setModalContent('');
      setModalFolderId('');
      setModalKeywords([]);
    }
    setModalKeywordInput('');
    setIsModalKeywordInputOpen(false);
    setMemoryDraftError('');
    setIsModalOpen(true);
  }

  async function copyAssistantMessage(message: ChatMessage) {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopiedAssistantMessageId(message.id);
      window.setTimeout(() => setCopiedAssistantMessageId((current) => current === message.id ? null : current), 1500);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '复制失败，请稍后重试');
      setStatusMessage('');
    }
  }

  function resetMemoryDraft() {
    setModalTitle('');
    setModalContent('');
    setModalFolderId('');
    setModalKeywords([]);
    setModalKeywordInput('');
    setIsModalKeywordInputOpen(false);
    setMemoryDraftError('');
    setStatusMessage('');
    setErrorMessage('');
  }

  async function handleParseMemory() {
    if (!modalContent.trim() || parsingMemory) return;
    setParsingMemory(true);
    try {
      const response = await api.parseMemory(modalContent, modalTitle, memoryFolders.map((folder) => folder.name));
      const parsedTitle = response.draft.title || modalTitle;
      setModalTitle(parsedTitle);
      setModalKeywords(response.draft.keywords.filter((keyword) => keyword !== modalStatusTag));
      const parsedFolder = response.draft.folder.trim().toLocaleLowerCase();
      const matchedFolder = memoryFolders.find(
        (folder) => folder.name.trim().toLocaleLowerCase() === parsedFolder
          || folder.id.trim().toLocaleLowerCase() === parsedFolder,
      );
      if (matchedFolder) setModalFolderId(matchedFolder.id);
      if (memoryDraftError === 'title' && parsedTitle.trim()) setMemoryDraftError('');
      if (memoryDraftError === 'folder' && matchedFolder) setMemoryDraftError('');
      setStatusMessage('AI 已生成记忆卡草稿，请确认后保存');
      setErrorMessage('');
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'AI 解析失败，请检查模型配置');
      setStatusMessage('');
    } finally {
      setParsingMemory(false);
    }
  }

  function addModalKeyword() {
    const nextKeyword = modalKeywordInput.trim();
    if (!nextKeyword) return;
    if (modalMode !== 'edit' && nextKeyword === modalStatusTag) {
      setModalKeywordInput('');
      return;
    }
    if (modalKeywords.includes(nextKeyword)) return;
    setModalKeywords((current) => [...current, nextKeyword]);
    setModalKeywordInput('');
    setIsModalKeywordInputOpen(false);
  }

  function handleModalKeywordKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addModalKeyword();
  }

  async function handleOpenQuickAdd() {
    if (isTauriRuntime) {
      await invoke('show_quick_add_window');
      return;
    }
    openModal('add');
  }

  async function handleCloseQuickAdd() {
    if (isTauriRuntime) {
      await invoke('hide_quick_add_window');
      return;
    }
    setIsModalOpen(false);
  }

  function handleCloseModal() {
    resetMemoryDraft();
    setIsModalOpen(false);
  }

  function handleQuickAddDrag() {
    void invoke('start_quick_add_drag').catch((error) => {
      setErrorMessage(error instanceof Error ? error.message : '弹窗移动失败');
    });
  }

  async function handleCreateMemory(event: FormEvent) {
    event.preventDefault();
    if (!isQuickAddWindow && modalMode !== 'edit') {
      if (!modalTitle.trim()) {
        setMemoryDraftError('title');
        return;
      }
      if (!modalContent.trim()) {
        setMemoryDraftError('content');
        return;
      }
      if (!modalFolderId) {
        setMemoryDraftError('folder');
        return;
      }
    }
    if (!modalContent.trim() || parsingMemory) {
      setErrorMessage('请输入记忆信息');
      return;
    }
    if (!isQuickAddWindow && !modalTitle.trim()) {
      setErrorMessage('请输入记忆卡标题');
      return;
    }
    let title = modalTitle.trim();
    let content = modalContent.trim();
    let folderId = modalFolderId || null;
    let keywords = modalMode === 'edit' ? modalKeywords : [modalStatusTag, ...modalKeywords];

    try {
      if (modalMode === 'edit' && selectedMemory) {
        const updated = await api.updateMemory(selectedMemory.id, {
          title,
          content,
          type: selectedMemory.type,
          folder_id: folderId,
          keywords,
        });
        setMemories((current) => current.map((memory) => memory.id === updated.id ? updated : memory));
        setSelectedMemoryId(updated.id);
        resetMemoryDraft();
        setIsModalOpen(false);
        setStatusMessage('记忆卡已保存');
        return;
      }

      if (isQuickAddWindow) {
        setParsingMemory(true);
        setErrorMessage('');
        setStatusMessage('正在识别标题、分组和关键词...');
        const response = await api.parseMemory(content, '', memoryFolders.map((folder) => folder.name));
        const parsedFolder = response.draft.folder.trim().toLocaleLowerCase();
        const matchedFolder = memoryFolders.find(
          (folder) => folder.name.trim().toLocaleLowerCase() === parsedFolder
            || folder.id.trim().toLocaleLowerCase() === parsedFolder,
        );
        title = response.draft.title.trim() || sessionTitle(content);
        content = response.draft.content.trim() || content;
        folderId = matchedFolder?.id ?? null;
        keywords = [
          modalStatusTag,
          ...response.draft.keywords.filter((keyword) => keyword.trim() && keyword.trim() !== modalStatusTag).slice(0, 4),
        ];
      }

      const created = await api.createMemory({
        title,
        content,
        type: '经验型',
        folder_id: folderId,
        source: modalMode === 'save' ? '对话沉淀' : '手动添加',
        keywords,
      });
      setMemories((current) => [created, ...current]);
      setSelectedMemoryId(created.id);
      resetMemoryDraft();
      if (isQuickAddWindow) {
        await emitTo('main', 'memory-created', created);
        setStatusMessage('已保存为记忆');
        await new Promise(resolve => window.setTimeout(resolve, 700));
        await handleCloseQuickAdd();
        setStatusMessage('');
      } else {
        showMemoryUploadToast(created);
        setIsModalOpen(false);
        openView('memory-detail');
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '记忆卡创建失败');
      setStatusMessage('');
    } finally {
      if (isQuickAddWindow) setParsingMemory(false);
    }
  }

  function openMemoryDeleteConfirm(memory: MemoryCard) {
    setPendingDeleteMemory(memory);
  }

  function closeMemoryDeleteConfirm() {
    if (deletingMemory) return;
    setPendingDeleteMemory(null);
  }

  async function deleteMemory(memory: MemoryCard) {
    setDeletingMemory(true);
    try {
      await api.deleteMemory(memory.id);
      const nextMemories = memories.filter((item) => item.id !== memory.id);
      setMemories(nextMemories);
      setSelectedMemoryId(nextMemories[0]?.id ?? null);
      setManualMemoryIds((current) => current.filter((id) => id !== memory.id));
      setStatusMessage('记忆卡已删除');
      setErrorMessage('');
      setPendingDeleteMemory(null);
      if (view === 'memory-detail') openView('memories');
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '记忆卡删除失败');
    } finally {
      setDeletingMemory(false);
    }
  }

  async function handleSaveConfig(event: FormEvent) {
    event.preventDefault();
    if (!configLoaded || loadingConfig) {
      setErrorMessage('配置仍在加载中，请稍后再保存');
      setStatusMessage('');
      return;
    }
    setSavingConfig(true);
    try {
      const response = await api.saveConfig(configForSubmit(config, hasStoredChatApiKey, hasStoredEmbeddingApiKey));
      setConfig(response);
      setHasStoredChatApiKey(response.api_key === MASKED_CONFIG_SECRET);
      setHasStoredEmbeddingApiKey(response.embedding_api_key === MASKED_CONFIG_SECRET);
      setConfigLoaded(true);
      setStatusMessage('模型配置已保存');
      setErrorMessage('');
      setIsConfigModalOpen(false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '配置保存失败');
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleTestConfig() {
    if (!configLoaded || loadingConfig) {
      setErrorMessage('配置仍在加载中，请稍后再测试');
      setStatusMessage('');
      return;
    }
    setTestingConfig(true);
    try {
      const submittedConfig = configForSubmit(config, hasStoredChatApiKey, hasStoredEmbeddingApiKey);
      await api.testConfig(configTab, submittedConfig);
      const testedModel = configTab === 'embedding' ? submittedConfig.embedding_model : submittedConfig.model;
      setStatusMessage(`${configTab === 'embedding' ? 'Embedding' : '对话模型'}连通性测试成功：${testedModel}`);
      setErrorMessage('');
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : `${configTab === 'embedding' ? 'Embedding' : '对话模型'}连通性测试失败`);
      setStatusMessage('');
    } finally {
      setTestingConfig(false);
    }
  }

  function renderMemoryPicker() {
    if (!MEMORY_RECALL_ENABLED || !memoryPickerTarget) return null;
    const selectionUnchanged = memoryPickerSelectedIds.length === manualMemoryIds.length
      && memoryPickerSelectedIds.every((memoryId) => manualMemoryIds.includes(memoryId));
    const addToConversationDisabled = memoryPickerSelectedIds.length === 0
      || (manualMemoryIds.length >= MAX_MANUAL_MEMORY_IDS && selectionUnchanged);

    return (
      <div className="desktop-memory-picker-modal-mask" onClick={closeMemoryPicker}>
        <div
          className="desktop-memory-picker-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="memory-picker-title"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="desktop-memory-picker-modal__head">
            <h3 id="memory-picker-title">选择记忆卡</h3>
            <button
              className="desktop-memory-picker-modal__close"
              type="button"
              aria-label="关闭"
              onClick={closeMemoryPicker}
            >
              ×
            </button>
          </div>

          <label className="desktop-memory-picker-modal__search">
            <input
              autoFocus
              value={memoryPickerQuery}
              onChange={(event) => setMemoryPickerQuery(event.target.value)}
              placeholder="请输入记忆卡名称"
            />
            <img src={pickerSearchIcon} alt="" />
          </label>

          <div className="desktop-memory-picker-modal__body">
            <aside className="desktop-memory-picker-modal__folders" aria-label="记忆卡分组">
              <span className="desktop-memory-picker-modal__label">分组</span>
              <button
                className={`desktop-memory-picker-modal__folder ${memoryPickerFolderId === null ? 'is-active' : ''}`}
                type="button"
                onClick={() => setMemoryPickerFolderId(null)}
              >
                <img src={pickerFolderOpenIcon} alt="" />
                <span>全部记忆</span>
              </button>
              {memoryFolders.map((folder) => (
                <button
                  className={`desktop-memory-picker-modal__folder ${memoryPickerFolderId === folder.id ? 'is-active' : ''}`}
                  key={folder.id}
                  type="button"
                  onClick={() => setMemoryPickerFolderId(folder.id)}
                >
                  <img src={pickerFolderOpenIcon} alt="" />
                  <span>{folder.name}</span>
                </button>
              ))}
            </aside>
            <section className="desktop-memory-picker-modal__cards">
              {memoryPickerMemories.length ? memoryPickerMemories.map((memory) => {
                const checked = memoryPickerSelectedIds.includes(memory.id);
                const disabled = !checked && memoryPickerSelectedIds.length >= MAX_MANUAL_MEMORY_IDS;
                return (
                  <label
                    className={`desktop-memory-picker-modal__card ${checked ? 'is-selected' : ''} ${disabled ? 'is-disabled' : ''}`}
                    key={memory.id}
                    title={disabled ? MEMORY_PICKER_LIMIT_TOOLTIP : undefined}
                  >
                    <span className="desktop-memory-picker-modal__checkbox">
                      <input
                        checked={checked}
                        aria-disabled={disabled}
                        type="checkbox"
                        onChange={() => toggleMemoryPickerSelection(memory.id)}
                      />
                      <img src={pickerCheckIcon} alt="" />
                    </span>
                    <span className="desktop-memory-picker-modal__card-icon">
                      <img src={pickerPlayingCardsFanIcon} alt="" />
                    </span>
                    <span className="desktop-memory-picker-modal__card-copy">
                      <strong>{memory.title}</strong>
                      <span>{memory.content}</span>
                    </span>
                  </label>
                );
              }) : (
                <div className="desktop-memory-picker-modal__empty">暂无可选记忆卡</div>
              )}
            </section>
          </div>

          <div className="desktop-memory-picker-modal__footer">
            <span>已选 {memoryPickerSelectedIds.length}/{MAX_MANUAL_MEMORY_IDS} 个文件</span>
            <button
              className="desktop-memory-picker-modal__confirm"
              type="button"
              disabled={addToConversationDisabled}
              onClick={confirmMemoryPicker}
            >
              <span aria-hidden="true">+</span>
              添加到对话
            </button>
          </div>
        </div>
      </div>
    );
  }

  function renderHomeComposer() {
    const renderSelectedMemories = () => (
      <div className="desktop-composer__selected-memories" aria-label="已选择的记忆卡">
        {selectedHomeMemories.map((memory) => {
          return (
            <article
              className="desktop-composer-memory-card"
              key={memory.id}
              title={memory.title}
            >
              <span className="desktop-composer-memory-card__icon">
                <img src={iconForChatMemory(memory, memoryFolders)} alt="" />
                <button
                  className="desktop-composer-memory-card__remove"
                  type="button"
                  aria-label={`移除 ${memory.title}`}
                  onClick={() => toggleManualMemory(memory.id)}
                >
                  ×
                </button>
              </span>
              <strong>{truncateMemoryTitle(memory.title)}</strong>
            </article>
          );
        })}
      </div>
    );

    return (
      <div ref={homeComposerRef} className="desktop-home-composer-wrap">
        <form
          className={`desktop-composer desktop-home-composer ${selectedHomeMemories.length ? 'has-selected-memories' : ''}`}
          onSubmit={handleHomeSubmit}
        >
          <div className="desktop-composer__scroll-content">
            {selectedHomeMemories.length ? renderSelectedMemories() : null}
            <textarea
              aria-label="输入问题"
              placeholder={HOME_PROMPT}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onInput={resizeComposerTextarea}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' || event.shiftKey) return;
                if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
                event.preventDefault();
                if (!sendingChat && prompt.trim()) void sendChat(prompt, manualMemoryIds, true);
              }}
            />
          </div>
          <div className="desktop-composer__footer">
            <button className="desktop-add-memory" type="button" onClick={() => openMemoryPicker('home')}>
              <span className="desktop-add-memory__icon">
                <img alt="" src={addMemoryIcon} />
              </span>
              <span>选择记忆</span>
            </button>
            <button
              className={`desktop-send ${sendingChat ? 'is-interrupting' : ''}`}
              type={sendingChat ? 'button' : 'submit'}
              aria-label={sendingChat ? '中断生成' : '发送'}
              disabled={!prompt.trim() && !sendingChat}
              onClick={sendingChat ? interruptChat : undefined}
            >
              {sendingChat ? <span className="desktop-send__stop" /> : <img alt="" src={sendIcon} />}
            </button>
          </div>
        </form>
        <p className="desktop-home-composer__disclaimer">内容由AI生成，请核实重要信息</p>
      </div>
    );
  }

  const memoryDraftFields = (
    <>
      <label className={`desktop-field ${memoryDraftError === 'title' ? 'has-error' : ''}`}>
        <span>标题</span>
        <input
          placeholder="请输入记忆卡标题"
          value={modalTitle}
          onChange={(event) => {
            setModalTitle(event.target.value);
            if (memoryDraftError === 'title') setMemoryDraftError('');
          }}
        />
        {memoryDraftError === 'title' ? <small className="desktop-field__error">请输入记忆卡标题</small> : null}
      </label>
      <label className={`desktop-field ${memoryDraftError === 'content' ? 'has-error' : ''}`}>
        <div className="desktop-field__label-row">
          <span>记忆卡信息</span>
          <button
            className="desktop-text-button"
            type="button"
            disabled={parsingMemory || !modalContent.trim()}
            onClick={() => void handleParseMemory()}
          >
            {parsingMemory ? '解析中...' : '快速解析'}
          </button>
        </div>
        <div className="desktop-memory-info-field">
          <textarea
            maxLength={2000}
            placeholder="请输入详细介绍"
            value={modalContent}
            onChange={(event) => {
              setModalContent(event.target.value);
              if (memoryDraftError === 'content') setMemoryDraftError('');
            }}
          />
          <span className="desktop-memory-info-field__count">{modalContent.length}/2000</span>
        </div>
        {memoryDraftError === 'content' ? <small className="desktop-field__error">请输入记忆卡信息</small> : null}
      </label>
      <label className={`desktop-field ${memoryDraftError === 'folder' ? 'has-error' : ''}`}>
        <span>分组</span>
        <div className="desktop-select-wrap">
          <select
            value={modalFolderId}
            onChange={(event) => {
              setModalFolderId(event.target.value);
              if (memoryDraftError === 'folder') setMemoryDraftError('');
            }}
          >
            <option value="">请选择分组</option>
            {memoryFolders.map((folder) => (
              <option key={folder.id} value={folder.id}>{folder.name}</option>
            ))}
          </select>
        </div>
        {memoryDraftError === 'folder' ? <small className="desktop-field__error">请选择分组</small> : null}
      </label>
      <div className="desktop-field">
        <div className="desktop-field__label-row">
          <span>关键词 <em>（{Math.min(5, modalKeywords.length + (modalMode === 'edit' ? 0 : 1))}/5）</em></span>
        </div>
        <div className="desktop-tag-section">
          <div className="desktop-tag-list">
            {modalMode !== 'edit' ? (
              <span className="desktop-chip desktop-chip--accent">{modalStatusTag}</span>
            ) : null}
            {modalKeywords.map((keyword) => (
              <button
                className="desktop-tag-chip"
                key={keyword}
                type="button"
                onClick={() => setModalKeywords((current) => current.filter((item) => item !== keyword))}
              >
                {keyword}
                <span aria-hidden="true">×</span>
              </button>
            ))}
            {isModalKeywordInputOpen ? (
              <input
                className="desktop-tag-input"
                autoFocus
                placeholder="输入关键词"
                value={modalKeywordInput}
                onChange={(event) => setModalKeywordInput(event.target.value)}
                onKeyDown={handleModalKeywordKeyDown}
              />
            ) : (
              <button
                className="desktop-tag-add"
                type="button"
                aria-label="新增关键词"
                onClick={() => setIsModalKeywordInputOpen(true)}
              />
            )}
          </div>
        </div>
      </div>
    </>
  );

  if (isQuickAddWindow) {
    return (
      <div className="quick-add-window">
        <div
          className="quick-add-window__frame"
          onMouseDown={(event) => {
            if (event.button === 0) handleQuickAddDrag();
          }}
        />
        <form className="quick-add-card" onSubmit={handleCreateMemory}>
          <div className="quick-add-card__head">
            <div
              className="quick-add-card__drag"
              onMouseDown={(event) => {
                if (event.button === 0) handleQuickAddDrag();
              }}
            >
              <h1>快速记忆</h1>
              <span className="quick-add-shortcut">{quickShortcut}</span>
            </div>
            <button className="quick-add-card__close" type="button" aria-label="关闭" onClick={() => void handleCloseQuickAdd()} />
          </div>
          {errorMessage ? <span className="desktop-status desktop-status--error">{errorMessage}</span> : null}
          {!errorMessage && statusMessage ? <span className={`desktop-status${statusMessage === '已保存为记忆' ? ' desktop-status--ok' : ''}`} role="status">{statusMessage}</span> : null}
          <textarea
            className="quick-add-card__content"
            autoFocus
            aria-label="请输入记忆信息"
            placeholder="记下一个结论、待办，或以后还会用到的信息"
            disabled={parsingMemory}
            value={modalContent}
            onChange={(event) => setModalContent(event.target.value)}
          />
          <div className="quick-add-card__footer">
            <button className="desktop-primary-button" type="submit" disabled={!modalContent.trim() || parsingMemory}>
              {statusMessage === '已保存为记忆' ? '已保存' : parsingMemory ? '识别中...' : '保存为记忆卡'}
            </button>
          </div>
        </form>
      </div>
    );
  }

  if (checkingAuth || !authSession) {
    return (
      <div className="desktop-auth-page" onMouseDownCapture={handleWindowDrag}>
        <video
          className="desktop-auth-page__video"
          src={loginBackgroundVideo}
          autoPlay
          loop
          muted
          playsInline
          aria-hidden="true"
        />
        <div
          className="desktop-window-controls"
          aria-label="窗口控制与拖动区域"
          data-tauri-drag-region="deep"
        >
          <button
            className="desktop-window-control desktop-window-control--close"
            type="button"
            aria-label="关闭窗口"
            onClick={() => void handleWindowAction('close')}
          />
          <button
            className="desktop-window-control desktop-window-control--minimize"
            type="button"
            aria-label="最小化窗口"
            onClick={() => void handleWindowAction('minimize')}
          />
          <button
            className="desktop-window-control desktop-window-control--maximize"
            type="button"
            aria-label="最大化窗口"
            onClick={() => void handleWindowAction('maximize')}
          />
        </div>
        <section className="desktop-auth-card" data-tauri-drag-region="false">
          <img className="desktop-auth-card__logo" src={logoImage} alt="记忆大师" />
          <div className="desktop-auth-card__head">
            <h1>{authMode === 'login' ? '欢迎回到记忆大师' : '创建账号'}</h1>
          </div>
          {checkingAuth ? (
            <div className="desktop-auth-card__loading">
              <Ring size="22" stroke="3" bgOpacity="0.2" speed="1.8" color="#111111" />
              <span>正在确认登录状态...</span>
            </div>
          ) : (
            <form className="desktop-auth-form" onSubmit={handleAuthSubmit}>
              <label className="desktop-auth-field">
                <span>账号</span>
                <input
                  autoFocus
                  value={authAccount}
                  onChange={(event) => setAuthAccount(event.target.value)}
                  placeholder="请输入账号"
                />
              </label>
              {authMode === 'register' ? (
                <label className="desktop-auth-field">
                  <span>昵称</span>
                  <input
                    value={authDisplayName}
                    onChange={(event) => setAuthDisplayName(event.target.value)}
                    placeholder="可选"
                  />
                </label>
              ) : null}
              <label className="desktop-auth-field">
                <span>密码</span>
                <input
                  type="password"
                  value={authPassword}
                  onChange={(event) => setAuthPassword(event.target.value)}
                  placeholder="至少 6 位"
                />
              </label>
              {authError ? <span className="desktop-status desktop-status--error">{authError}</span> : null}
              <button
                className="desktop-primary-button desktop-auth-form__submit"
                type="submit"
                disabled={!authAccount.trim() || !authPassword || authSubmitting}
              >
                {authSubmitting ? '处理中...' : authMode === 'login' ? '登录' : '注册并进入'}
              </button>
              <button
                className="desktop-auth-form__switch"
                type="button"
                onClick={() => {
                  setAuthMode(authMode === 'login' ? 'register' : 'login');
                  setAuthError('');
                }}
              >
                {authMode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
              </button>
            </form>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className={`desktop-homepage ${view === 'home' ? 'is-home' : ''}`} onMouseDownCapture={handleWindowDrag}>
      <div
        className="desktop-window-controls"
        aria-label="窗口控制与拖动区域"
        data-tauri-drag-region="deep"
      >
        <button
          className="desktop-window-control desktop-window-control--close"
          type="button"
          aria-label="关闭窗口"
          onClick={() => void handleWindowAction('close')}
        />
        <button
          className="desktop-window-control desktop-window-control--minimize"
          type="button"
          aria-label="最小化窗口"
          onClick={() => void handleWindowAction('minimize')}
        />
        <button
          className="desktop-window-control desktop-window-control--maximize"
          type="button"
          aria-label="最大化窗口"
          onClick={() => void handleWindowAction('maximize')}
        />
      </div>
      <aside className="desktop-sidebar">
        <img className="desktop-sidebar__brand" src={logoImage} alt="记忆大师" />
        <nav className="desktop-sidebar__nav" aria-label="主导航">
          {navItems.map((item) => {
            const active =
              item.action === 'add-memory'
                ? false
                : item.action === 'open-history'
                  ? isChatView && isHistoryDrawerOpen
                : item.view === 'memories'
                ? view === 'memories' || view === 'memory-detail'
                : item.view === 'history'
                  ? view === 'history'
                  : view === item.view;
            return (
              <div className="desktop-nav-group" key={item.label}>
                <button
                  className={`desktop-nav ${item.className} ${active ? 'is-active' : ''}`}
                  type="button"
                  onClick={() => {
                    if (item.action === 'add-memory') {
                      openModal('add');
                      return;
                    }
                    if (item.action === 'open-history') {
                      openLatestHistorySession();
                      return;
                    }
                    openView(item.view);
                  }}
                >
                  <span className={`desktop-nav__icon ${item.iconClassName ?? ''}`}>
                    <img alt="" src={item.icon} />
                  </span>
                  <span className="desktop-nav__label">{item.label}</span>
                </button>
                {item.dividerAfter ? <span className="desktop-nav__divider" aria-hidden="true" /> : null}
              </div>
            );
          })}
        </nav>
        <div className="desktop-sidebar__settings-wrap" data-settings-menu>
          {isSettingsMenuOpen ? (
            <div className="desktop-settings-menu" role="menu">
              {isTauriRuntime && <QuickEntrySetting />}
              <button
                className="desktop-settings-menu__item"
                type="button"
                role="menuitem"
                onClick={openConfigModal}
              >
                模型API
              </button>
              <button
                className="desktop-settings-menu__item is-danger"
                type="button"
                role="menuitem"
                onClick={() => void handleLogout()}
              >
                退出登录
              </button>
            </div>
          ) : null}
          <button
            className={`desktop-sidebar__settings ${isSettingsMenuOpen ? 'is-open' : ''}`}
            type="button"
            aria-label="设置"
            aria-expanded={isSettingsMenuOpen}
            onClick={() => setIsSettingsMenuOpen((current) => !current)}
          >
            <img alt="" src={navSettingsIcon} />
          </button>
        </div>
      </aside>

      <main className="desktop-main">
        {canShowHistoryDrawer ? (
          <button
            className={`desktop-main__collapse ${isHistoryDrawerOpen ? 'is-open' : ''}`}
            type="button"
            aria-label={isHistoryDrawerOpen ? '收起历史记录' : '展开历史记录'}
            data-tauri-drag-region="false"
            onClick={() => setIsHistoryDrawerOpen((current) => !current)}
          >
            <img alt="" src={panelLeftIcon} />
          </button>
        ) : null}

        {canShowHistoryDrawer && isHistoryDrawerOpen ? (
          <aside className="desktop-history-drawer">
            <h2>历史对话</h2>
            <div className="desktop-history-drawer__search">
              <input
                value={historySearchQuery}
                onChange={(event) => setHistorySearchQuery(event.target.value)}
                placeholder="搜索任务"
              />
              <img src={pickerSearchIcon} alt="" />
            </div>
            <button className="desktop-history-drawer__new-question" type="button" onClick={startNewQuestion}>
              <span aria-hidden="true">＋</span>
              <span>新对话</span>
            </button>

            <div className="desktop-history-drawer__list">
              {!filteredHistorySessions.length ? (
                <div className="desktop-history-drawer__empty">
                  {sortedSessions.length ? '没有匹配的历史记录' : '还没有历史会话，先发一次问。'}
                </div>
              ) : null}

              {filteredHistorySessions.map((session) => (
                <button
                  className={`desktop-history-drawer__item ${session.id === activeChatSessionId ? 'is-active' : ''}`}
                  key={session.id}
                  type="button"
                  onContextMenu={(event) => openChatContextMenu(event, session.id)}
                  onClick={() => openChatSession(session)}
                >
                  <div className="desktop-history-drawer__item-head">
                    <strong title={session.title}>{session.title}</strong>
                  </div>
                </button>
              ))}
            </div>
            {chatContextMenu ? (
              <div
                className="desktop-history-drawer__context-menu"
                data-chat-context-menu
                role="menu"
                style={{ left: chatContextMenu.x, top: chatContextMenu.y }}
              >
                <button type="button" role="menuitem" onClick={renameChatSession}>
                  重命名
                </button>
                <button className="is-danger" type="button" role="menuitem" onClick={deleteChatSession}>
                  删除
                </button>
              </div>
            ) : null}
          </aside>
        ) : null}

        <div
          className={`desktop-main__content ${canShowHistoryDrawer && isHistoryDrawerOpen ? 'has-history-drawer' : ''} ${
            view === 'home' ? 'is-home' : ''
          }`}
        >
        {view === 'home' ? (
          <section className={`desktop-home ${homeMemoryGroup ? 'is-drilldown' : ''}`}>
            {activeHomeMemoryGroup ? (
              <div className="desktop-home-drilldown">
                <div className="desktop-home-drilldown__hero">
                  <h1>{HOME_TITLE}<span>记忆大师</span></h1>
                  <span>{HOME_SLOGAN}</span>
                </div>
                <div className="desktop-home-drilldown__content">
                  <div className="desktop-home-drilldown__toolbar">
                    <button
                      className="desktop-home-back"
                      type="button"
                      onClick={() => {
                        setHomeMemoryGroup(null);
                        setHomeMemoryPage(0);
                      }}
                    >
                      <img alt="" src={chevronLeftIcon} />
                      <span>{activeHomeMemoryGroup.title}</span>
                    </button>
                    <div className="desktop-home-drilldown__controls">
                      <span>{Math.min(homeMemoryPage + 1, homeMemoryPageCount)} / {homeMemoryPageCount}</span>
                      <button
                        type="button"
                        aria-label="上一页"
                        disabled={homeMemoryPage === 0}
                        onClick={() => setHomeMemoryPage((current) => Math.max(0, current - 1))}
                      >
                        <img alt="" src={chevronLeftIcon} />
                      </button>
                      <button
                        type="button"
                        aria-label="下一页"
                        disabled={homeMemoryPage >= homeMemoryPageCount - 1}
                        onClick={() => setHomeMemoryPage((current) => Math.min(homeMemoryPageCount - 1, current + 1))}
                      >
                        <img alt="" src={chevronRightIcon} />
                      </button>
                    </div>
                  </div>

                  <div className="desktop-home-drilldown__grid">
                    {visibleHomeGroupMemories.length ? visibleHomeGroupMemories.map((memory) => {
                      const iconMeta = memoryCardFolderMeta(memory, memoryFolders);
                      return (
                        <button
                          className="desktop-home-detail-card"
                          key={memory.id}
                          type="button"
                          onClick={() => addRecentMemoryToComposer(memory.id)}
                        >
                          <span className={`desktop-home-detail-card__icon is-${iconMeta.accent}`}>
                            <img alt="" src={iconMeta.icon} />
                          </span>
                          <span className="desktop-home-detail-card__body">
                            <strong>{memory.title}</strong>
                            <span>{memory.content}</span>
                          </span>
                        </button>
                      );
                    }) : (
                      <div className="desktop-home-empty">
                        <span>还没有归入此分组的记忆卡</span>
                        <button type="button" onClick={() => openModal('add')}>新增一张记忆卡</button>
                      </div>
                    )}
                  </div>
                </div>
                {renderHomeComposer()}
              </div>
            ) : (
              <div
                className="desktop-home-overview"
                ref={homeOverviewRef}
                style={{
                  '--home-card-scale': homeCardScale,
                  '--home-card-layout-width': `${homeCardLayoutWidth}px`,
                } as CSSProperties}
              >
                <div className="desktop-home-overview__head">
                  <h1>{HOME_TITLE}<span>记忆大师</span></h1>
                  <span>{HOME_SLOGAN}</span>
                </div>

                <div
                  className={`desktop-home-card-stage is-count-${homeDisplayFolders.length + 1}`}
                  ref={homeCardStageRef}
                >
                  {homeDisplayFolders.map((folder, index) => {
                    const group = homeGroupMeta(folder);
                    const groupCount = memories.filter((memory) => memoryMatchesHomeGroup(memory, folder.id)).length;
                    return (
                      <button
                        className={`desktop-home-group-card is-${group.accent} ${group.accent === 'generic' ? 'is-generic' : ''} is-card-${index + 1}`}
                        key={folder.id}
                        type="button"
                        aria-label={`${group.title}，${groupCount} 张记忆卡`}
                        style={{
                          '--home-group-card-bg': group.background,
                          '--home-group-card-title': group.textColor,
                          '--home-group-card-arrow': group.arrowColor,
                        } as CSSProperties}
                        onClick={() => {
                          setHomeMemoryGroup(folder.id);
                          setHomeMemoryPage(0);
                        }}
                      >
                        <span className="desktop-home-group-card__icon">
                          <img src={folder.name === '会议纪要' ? homeIconTodos : folder.name === '日常待办' ? homeIconMeetings : group.icon} alt="" />
                        </span>
                        <strong className="desktop-home-group-card__title">{folder.name}</strong>
                        <span className="desktop-home-group-card__summary">{groupCount ? `${groupCount}条记忆` : '暂无记忆'}</span>
                        <span className="desktop-home-group-card__open" aria-hidden="true">
                          <img src={cardOpenArrowIcon} alt="" />
                        </span>
                      </button>
                    );
                  })}
                  <button
                    className={`desktop-home-group-card desktop-home-group-card--add is-card-${homeDisplayFolders.length + 1}`}
                    type="button"
                    onClick={() => openModal('add')}
                  >
                    <span className="desktop-home-group-card__add-icon">+</span>
                    <span className="desktop-home-group-card__title">新增记忆</span>
                  </button>
                </div>

                {renderHomeComposer()}
              </div>
            )}
          </section>
        ) : null}

        {view !== 'home' ? (
            <section className={`desktop-workspace ${view === 'memories' ? 'desktop-workspace--memories' : ''} ${
              isChatView ? 'desktop-workspace--chat' : ''
            } ${view === 'memory-detail' ? 'desktop-workspace--memory-detail' : ''}`}>
            <header className={`desktop-page-head ${view === 'memories' ? 'desktop-page-head--memories' : ''} ${
              view === 'memory-detail' ? 'desktop-page-head--memory-detail' : ''
            }`}>
              <div>
                {!isChatView ? (
                  <h2 className="desktop-page-title">
                    {view === 'memories' && '记忆卡'}
                    {view === 'memory-detail' && '记忆卡详情'}
                  </h2>
                ) : null}
                <p className="desktop-page-subtitle">
                  {view === 'memories' && '将之前的记忆卡列表与编辑能力带回客户端。'}
                  {view === 'memory-detail' && '沿用旧原型的详情编辑逻辑，直接操作本地 SQLite 数据。'}
                  {isChatView && '对话页接回本地 Python API，保留引用与沉淀入口。'}
                </p>
              </div>
              <div className="desktop-inline-status">
                {statusMessage ? <span className="desktop-status desktop-status--ok">{statusMessage}</span> : null}
                {errorMessage ? <span className="desktop-status desktop-status--error">{errorMessage}</span> : null}
              </div>
            </header>

            {view === 'memories' ? (
              <div className="desktop-memory-shell">
                <div className="desktop-memory-manager">
                  <aside className="desktop-memory-folders">
                    <label className="desktop-search">
                      <input
                        value={memoryQuery}
                        onChange={(event) => setMemoryQuery(event.target.value)}
                        placeholder="搜索记忆卡名称"
                      />
                      <img src={pickerSearchIcon} alt="" />
                    </label>
                    <div className="desktop-memory-folders__head">
                      <h3>记忆卡分组</h3>
                      <button
                        className="desktop-memory-folder__add"
                        type="button"
                        aria-label="新增分组"
                        onClick={openCreateFolderModal}
                      >
                        +
                      </button>
                    </div>
                    <button
                      className={`desktop-memory-folder desktop-memory-folder--all ${memoryFolderFilter === null ? 'is-active' : ''}`}
                      type="button"
                      onClick={() => setMemoryFolderFilter(null)}
                    >
                      <span className="desktop-memory-folder__select">
                          <span>
                          <span className="desktop-memory-folder__icon is-blue">
                            <img src={pickerFolderOpenIcon} alt="" />
                          </span>
                          <span>全部记忆</span>
                        </span>
                        <small>{memories.length}</small>
                      </span>
                    </button>
                    {memoryFolders.map((folder) => {
                      const folderMeta = memoryFolderMeta(folder);
                      return (
                        <div
                          className={`desktop-memory-folder is-${folderMeta.accent} ${memoryFolderFilter === folder.id ? 'is-active' : ''}`}
                          key={folder.id}
                          onContextMenu={(event) => openMemoryFolderContextMenu(event, folder)}
                        >
                          <button
                            className="desktop-memory-folder__select"
                            type="button"
                            onClick={() => setMemoryFolderFilter(folder.id)}
                          >
                            <span>
                              <span className={`desktop-memory-folder__icon is-${folderMeta.accent}`}>
                                <img src={pickerFolderOpenIcon} alt="" />
                              </span>
                              {homeFolderIds.includes(folder.id) ? (
                                <span className="desktop-memory-folder__star" aria-label="首页展示">★</span>
                              ) : null}
                              <span>{folder.name}</span>
                            </span>
                            <small>{folder.memory_count}</small>
                          </button>
                        </div>
                      );
                    })}
                    {memoryFolderContextMenu ? (
                      <div
                        className={`desktop-memory-folder-context-menu ${memoryFolderContextMenu.folder.is_default ? 'is-default' : 'is-custom'}`}
                        data-memory-folder-context-menu
                        role="menu"
                        style={{ left: memoryFolderContextMenu.x, top: memoryFolderContextMenu.y }}
                      >
                        {!memoryFolderContextMenu.folder.is_default ? (
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => handleMemoryFolderRename(memoryFolderContextMenu.folder)}
                          >
                            重命名
                          </button>
                        ) : null}
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => handleMemoryFolderHomeToggle(memoryFolderContextMenu.folder)}
                        >
                          <span>
                            {homeFolderIds.includes(memoryFolderContextMenu.folder.id) ? '取消首页展示' : '设为首页展示'}
                          </span>
                        </button>
                        {!memoryFolderContextMenu.folder.is_default ? (
                          <button
                            className="is-danger"
                            type="button"
                            role="menuitem"
                            onClick={() => handleMemoryFolderDelete(memoryFolderContextMenu.folder)}
                          >
                            删除
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </aside>
                  <div className="desktop-memory-content">
                    <div className="desktop-memory-content__head">
                      <h2>全部记忆卡</h2>
                      <button className="desktop-memory-create-button" type="button" onClick={() => openModal('add')}>
                        <span aria-hidden="true">+</span>
                        <span>新建记忆卡</span>
                      </button>
                    </div>
                    <div className="desktop-memory-toolbar">
                      <select
                        className="desktop-memory-filter"
                        aria-label="按关键词筛选"
                        value={memoryKeywordFilter}
                        onChange={(event) => setMemoryKeywordFilter(event.target.value)}
                      >
                        <option value="">全部关键词</option>
                        {memoryKeywords.map((keyword) => <option key={keyword} value={keyword}>{keyword}</option>)}
                      </select>
                      <select
                        className="desktop-memory-filter"
                        value={memorySourceFilter}
                        onChange={(event) => setMemorySourceFilter(event.target.value)}
                        aria-label="按来源筛选"
                      >
                        <option value="">全部来源</option>
                        {memorySources.map((source) => <option key={source} value={source}>{source}</option>)}
                      </select>
                      <button className="desktop-secondary-button desktop-memory-external-add" type="button" onClick={() => void handleOpenQuickAdd()}>
                        外部添加 Ctrl+P
                      </button>
                    </div>

                    <div className="desktop-memory-list">
                      {loadingMemories ? <div className="desktop-empty-state">记忆卡加载中...</div> : null}
                      {!loadingMemories && !filteredMemories.length ? <div className="desktop-empty-state">暂无匹配记忆卡</div> : null}
                      {filteredMemories.map((memory) => {
                        const folder = memoryFolders.find((item) => item.id === memory.folder_id);
                        const folderMeta = folder ? memoryFolderMeta(folder) : {
                          icon: memoryFolderCustomIcon,
                          accent: 'blue' as const,
                        };
                        return (
                          <article
                            className={`desktop-memory-list-item is-${folderMeta.accent}`}
                            key={memory.id}
                            onClick={() => openMemoryDetail(memory.id)}
                          >
                            <span className={`desktop-memory-list-item__folder-icon is-${folderMeta.accent}`}>
                              <img src={folderMeta.icon} alt="" />
                            </span>
                            <div className="desktop-memory-list-item__body">
                              <h3>{memory.title}</h3>
                              <p>{memory.content}</p>
                              <div className="desktop-memory-list-item__tags">
                                <span className="desktop-chip">{folder?.name ?? '未分组'}</span>
                                {memory.source ? <span className="desktop-chip">{memory.source}</span> : null}
                                {memory.keywords.slice(0, 1).map((keyword) => <span className="desktop-chip" key={keyword}>{keyword}</span>)}
                              </div>
                            </div>
                            <div className="desktop-memory-list-item__update-row">
                              <span>{formatUpdateDateTime(memory.updated_at)} 更新</span>
                              <div className="desktop-memory-list-item__hover-actions">
                                <button
                                  className="desktop-memory-list-item__action"
                                  type="button"
                                  aria-label={`查看 ${memory.title} 详情`}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    openMemoryDetail(memory.id);
                                  }}
                                >
                                  <img src={penLineIcon} alt="" />
                                </button>
                                <button
                                  className="desktop-memory-list-item__action is-danger"
                                  type="button"
                                  aria-label={`删除 ${memory.title}`}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    openMemoryDeleteConfirm(memory);
                                  }}
                                >
                                  <img src={trashIcon} alt="" />
                                </button>
                              </div>
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            ) : null}

            {view === 'memory-detail' && selectedMemory ? (
              <div className="desktop-detail-shell">
                <div className="desktop-detail-main">
                  <div className="desktop-memory-detail__breadcrumb">
                    <button type="button" className="desktop-memory-detail__breadcrumb-back" onClick={goBackToMemories}>
                      <img src={chevronLeftIcon} alt="" />
                      <span>全部记忆卡</span>
                    </button>
                    <span>/</span>
                    <strong>记忆卡详情</strong>
                  </div>

                  <div className="desktop-memory-detail__summary">
                    <div className={`desktop-memory-detail__icon is-${memoryCardFolderMeta(selectedMemory, memoryFolders).accent}`}>
                      <img src={memoryCardFolderMeta(selectedMemory, memoryFolders).icon} alt="" />
                    </div>
                    <h1>{selectedMemory.title}</h1>
                    <div className="desktop-memory-detail__meta">
                      <span>文件夹：<strong>{memoryFolders.find((folder) => folder.id === selectedMemory.folder_id)?.name ?? '未分组'}</strong></span>
                      <i />
                      <span>更新时间：<strong>{formatUpdateDateTime(selectedMemory.updated_at).slice(0, 10)}</strong></span>
                      <i />
                      <span>引用次数：<strong>{selectedMemory.citations}</strong></span>
                    </div>
                    <div className="desktop-memory-detail__keywords">
                      {selectedMemory.keywords.map((keyword) => <span key={keyword}>{keyword}</span>)}
                    </div>
                  </div>

                  <div className="desktop-memory-detail__content-card">
                    <h2>记忆卡信息</h2>
                    <div className="desktop-memory-detail__content">
                      <MemoryMarkdown content={selectedMemory.content} />
                    </div>
                  </div>
                </div>

                <aside className="desktop-detail-side">
                  {MEMORY_RECALL_ENABLED ? (
                    <button
                      className="desktop-memory-detail__ask"
                      type="button"
                      onClick={() => {
                        setChatInput(`基于「${selectedMemory.title}」继续展开`);
                        openView('chat');
                      }}
                    >
                      基于记忆卡提问
                    </button>
                  ) : null}
                  <button
                    className="desktop-memory-detail__outline-button"
                    type="button"
                    onClick={() => openModal('edit')}
                  >
                    编辑记忆卡
                  </button>
                  <button
                    className="desktop-memory-detail__outline-button"
                    type="button"
                    onClick={() => openMemoryDeleteConfirm(selectedMemory)}
                  >
                    删除
                  </button>
                  <div className="desktop-memory-detail__network">
                    <h2>关联记忆网</h2>
                  </div>
                </aside>
              </div>
            ) : null}

            {isChatView ? (
              <div className="desktop-chat-shell">
                <div className="desktop-chat-main">
                  <div className="desktop-chat-body">
                    {!activeChatSession?.messages.length ? <div className="desktop-empty-state">输入问题后会在这里生成回答。</div> : null}
                    {activeChatSession?.messages.map((message) => (
                      <article
                        className={`desktop-bubble ${message.role === 'user' ? 'is-user' : 'is-assistant'} ${
                          message.role === 'assistant'
                            ? 'desktop-bubble--flat'
                            : 'desktop-bubble--question'
                        }`}
                        key={message.id}
                      >
                        {MEMORY_RECALL_ENABLED && message.role === 'user' && message.memoryIds?.length ? (
                          <div className="desktop-bubble-memory-list">
                            {message.memoryIds
                              .map((memoryId) => memories.find((memory) => memory.id === memoryId))
                              .filter((memory): memory is MemoryCard => Boolean(memory))
                              .map((memory) => (
                                <span className="desktop-bubble-memory-pill" key={memory.id} title={memory.title}>
                                  <img src={iconForChatMemory(memory, memoryFolders)} alt="" />
                                  {memory.title}
                                </span>
                              ))}
                          </div>
                        ) : null}
                        <MarkdownContent content={message.text} />
                        {message.role === 'assistant' ? (
                          <div className="desktop-bubble-actions">
                            <button type="button" onClick={() => void copyAssistantMessage(message)}>
                              <img src={copyIcon} alt="" />
                              {copiedAssistantMessageId === message.id ? '已复制' : '复制'}
                            </button>
                            {!message.failed ? <button type="button" onClick={() => openModal('save', message.text)}>
                              <img src={squarePlusIcon} alt="" />
                              添加到记忆卡
                            </button> : null}
                          </div>
                        ) : null}
                        {MEMORY_RECALL_ENABLED && message.role === 'assistant' && message.memoryUsage?.recalled_memories.length ? (
                          <div className={`desktop-bubble-citations ${collapsedRecallMessageIds.includes(message.id) ? 'is-collapsed' : ''}`}>
                            <button
                              className="desktop-bubble-citations__head"
                              type="button"
                              aria-expanded={!collapsedRecallMessageIds.includes(message.id)}
                              onClick={() => {
                                setCollapsedRecallMessageIds((current) => (
                                  current.includes(message.id)
                                    ? current.filter((id) => id !== message.id)
                                    : [...current, message.id]
                                ));
                              }}
                            >
                              <span>
                                主动引用 {message.memoryUsage.primary_citation_ids.length}
                                {' · '}
                                辅助召回 {message.memoryUsage.auto_recalled_memory_ids.length}
                              </span>
                              <img src={chevronRightIcon} alt="" />
                            </button>
                            {!collapsedRecallMessageIds.includes(message.id) ? (
                              <div className="desktop-bubble-recall-list">
                                {message.memoryUsage.recalled_memories.map((memory) => (
                                  <button
                                    className="desktop-bubble-recall-item"
                                    key={memory.id}
                                    type="button"
                                    onClick={() => openMemoryDetail(memory.id)}
                                  >
                                    <img src={iconForChatMemory(memory, memoryFolders)} alt="" />
                                    <span className="desktop-bubble-recall-item__content">
                                      <strong>{memory.title}</strong>
                                      <small>
                                        {message.memoryUsage?.primary_citation_ids.includes(memory.id)
                                          ? '主动引用'
                                          : memory.selection === 'manual'
                                            ? '手动选择，未形成引用'
                                            : '辅助召回'}
                                      </small>
                                    </span>
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                      </article>
                    ))}
                    {sendingChat ? (
                      <article className="desktop-chat-loading" aria-live="polite">
                        <Ring size={14} speed={1.6} stroke={2.5} bgOpacity={0.18} color="#111111" />
                        <span>{CHAT_LOADING_TEXT}</span>
                      </article>
                    ) : null}
                  </div>

                  <form
                    className={`desktop-composer desktop-chat-input ${selectedHomeMemories.length ? 'has-selected-memories' : ''}`}
                    onSubmit={handleChatSubmit}
                  >
                    <div className="desktop-composer__scroll-content">
                      {MEMORY_RECALL_ENABLED && selectedHomeMemories.length ? (
                        <div className="desktop-composer__selected-memories" aria-label="已选择的记忆卡">
                          {selectedHomeMemories.map((memory) => {
                            return (
                              <article
                                className="desktop-composer-memory-card"
                                key={memory.id}
                                title={memory.title}
                              >
                                <span className="desktop-composer-memory-card__icon">
                                  <img src={iconForChatMemory(memory, memoryFolders)} alt="" />
                                  <button
                                    className="desktop-composer-memory-card__remove"
                                    type="button"
                                    aria-label={`移除 ${memory.title}`}
                                    onClick={() => toggleManualMemory(memory.id)}
                                  >
                                    ×
                                  </button>
                                </span>
                                <strong>{truncateMemoryTitle(memory.title)}</strong>
                              </article>
                            );
                          })}
                        </div>
                      ) : null}
                      <textarea
                        placeholder="继续提问..."
                        value={chatInput}
                        onChange={(event) => setChatInput(event.target.value)}
                        onInput={resizeComposerTextarea}
                        onKeyDown={(event) => {
                          if (event.key !== 'Enter' || event.shiftKey) return;
                          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
                          event.preventDefault();
                          if (!sendingChat && chatInput.trim()) void sendChat();
                        }}
                      />
                    </div>
                    <div className="desktop-composer__footer desktop-chat-input__actions">
                      <button className="desktop-add-memory" type="button" onClick={() => openMemoryPicker('chat')}>
                        <span className="desktop-add-memory__icon">
                          <img alt="" src={addMemoryIcon} />
                        </span>
                        <span>选择记忆</span>
                      </button>
                      <button
                        className={`desktop-send ${sendingChat ? 'is-interrupting' : ''}`}
                        type={sendingChat ? 'button' : 'submit'}
                        disabled={!chatInput.trim() && !sendingChat}
                        aria-label={sendingChat ? '中断生成' : '发送'}
                        onClick={sendingChat ? interruptChat : undefined}
                      >
                        {sendingChat ? <span className="desktop-send__stop" /> : <img alt="" src={sendIcon} />}
                      </button>
                    </div>
                  </form>
                  <p className="desktop-home-composer__disclaimer desktop-chat-disclaimer">内容由AI生成，请核实重要信息</p>
                </div>
              </div>
            ) : null}

          </section>
        ) : null}
        </div>
      </main>

      {memoryLimitNotice > 0 ? (
        <div className="desktop-memory-limit-toast" role="status" aria-live="polite">
          最多可添加5张记忆卡
        </div>
      ) : null}

      {memoryUploadToast ? (
        <div className="desktop-memory-upload-toast" role="status">
          <div className="desktop-memory-upload-toast__head">
            <span className="desktop-memory-upload-toast__success" aria-hidden="true" />
            <strong>记忆卡上传成功</strong>
            <button
              className="desktop-memory-upload-toast__close"
              type="button"
              aria-label="关闭上传成功提示"
              onClick={() => setMemoryUploadToast(null)}
            >
              ×
            </button>
          </div>
          <div className="desktop-memory-upload-toast__body">
            <span className={`desktop-memory-upload-toast__icon is-${memoryUploadToast.accent}`}>
              <img src={memoryUploadToast.icon} alt="" />
            </span>
            <div>
              <p>{memoryUploadToast.title}</p>
              <span>所属文件{memoryUploadToast.folderName}</span>
            </div>
            <span className="desktop-memory-upload-toast__done" aria-hidden="true" />
          </div>
        </div>
      ) : null}

      {renderMemoryPicker()}

      {chatRenameSessionId ? (
        <div className="desktop-modal-mask" onClick={closeChatRenameModal}>
          <form
            className="desktop-modal desktop-chat-rename-modal"
            onSubmit={handleChatRenameSubmit}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="desktop-modal__head">
              <div>
                <h3>重命名</h3>
                <p>修改历史对话名称。</p>
              </div>
            </div>
            <label className="desktop-field">
              <span>对话名称</span>
              <input
                autoFocus
                maxLength={50}
                placeholder="请输入对话名称"
                value={chatRenameTitle}
                onChange={(event) => setChatRenameTitle(event.target.value)}
              />
            </label>
            <div className="desktop-modal__footer">
              <button className="desktop-secondary-button" type="button" onClick={closeChatRenameModal}>
                取消
              </button>
              <button className="desktop-primary-button" type="submit" disabled={!chatRenameTitle.trim()}>
                保存修改
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {folderModalMode ? (
        <div className="desktop-modal-mask" onClick={closeFolderModal}>
          <form
            className="desktop-modal desktop-folder-modal"
            onSubmit={(event) => void handleFolderSubmit(event)}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="desktop-modal__head">
              <div>
                <h3>{folderModalMode === 'create' ? '新增分组' : '修改分组名称'}</h3>
                <p>{folderModalMode === 'create' ? '创建一个新的记忆卡分组。' : '修改后，首页和记忆卡列表会同步更新。'}</p>
              </div>
            </div>
            <label className={`desktop-field ${folderModalError ? 'has-error' : ''}`}>
              <span>分组名称</span>
              <input
                autoFocus
                maxLength={30}
                placeholder="请输入分组名称"
                value={folderModalName}
                onChange={(event) => {
                  setFolderModalName(event.target.value);
                  if (folderModalError) setFolderModalError('');
                }}
              />
              {folderModalError ? <small className="desktop-field__error">{folderModalError}</small> : null}
            </label>
            <div className="desktop-modal__footer">
              <button className="desktop-secondary-button" type="button" onClick={closeFolderModal}>
                取消
              </button>
              <button className="desktop-primary-button" type="submit" disabled={!folderModalName.trim() || savingFolder}>
                {savingFolder ? '保存中...' : folderModalMode === 'create' ? '创建分组' : '保存修改'}
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {pendingDeleteFolder ? (
        <div className="desktop-modal-mask" onClick={closeFolderDeleteConfirm}>
          <div
            className="desktop-modal desktop-folder-delete-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="folder-delete-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="desktop-modal__head">
              <div>
                <h3 id="folder-delete-title">删除分组</h3>
                <p>确定删除“{pendingDeleteFolder.name}”吗？其中的记忆卡会变为未分组。</p>
              </div>
            </div>
            <div className="desktop-modal__footer">
              <button className="desktop-secondary-button" type="button" onClick={closeFolderDeleteConfirm} disabled={savingFolder}>
                取消
              </button>
              <button
                className="desktop-danger-button"
                type="button"
                onClick={() => void removeMemoryFolder(pendingDeleteFolder)}
                disabled={savingFolder}
              >
                {savingFolder ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {pendingDeleteMemory ? (
        <div className="desktop-modal-mask" onClick={closeMemoryDeleteConfirm}>
          <div
            className="desktop-modal desktop-memory-delete-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="memory-delete-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="desktop-modal__head">
              <div>
                <h3 id="memory-delete-title">删除记忆卡</h3>
                <p>确定删除“{pendingDeleteMemory.title}”吗？删除后无法恢复。</p>
              </div>
            </div>
            <div className="desktop-modal__footer">
              <button className="desktop-secondary-button" type="button" onClick={closeMemoryDeleteConfirm} disabled={deletingMemory}>
                取消
              </button>
              <button
                className="desktop-danger-button"
                type="button"
                onClick={() => void deleteMemory(pendingDeleteMemory)}
                disabled={deletingMemory}
              >
                {deletingMemory ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {isModalOpen ? (
        <div className="desktop-modal-mask" onClick={handleCloseModal}>
          <form
            className="desktop-modal desktop-memory-modal desktop-memory-editor"
            onSubmit={handleCreateMemory}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="desktop-modal__head">
              <div>
                <h3>{modalMode === 'edit' ? '编辑记忆卡' : '添加记忆卡'}</h3>
                <p>已有记忆卡信息想快速填写内容，可将内容粘贴到记忆卡信息后点击内容解析</p>
              </div>
              <button className="desktop-modal__close" type="button" aria-label="关闭" onClick={handleCloseModal}>
                ×
              </button>
            </div>

            <div className="desktop-memory-editor__fields">{memoryDraftFields}</div>

            <div className="desktop-modal__footer">
              <button className="desktop-secondary-button" type="button" onClick={handleCloseModal}>
                取消
              </button>
              <button className="desktop-primary-button" type="submit">
                {modalMode === 'edit' ? '保存修改' : '保存为记忆卡'}
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {isConfigModalOpen ? (
        <div className="desktop-modal-mask" data-tauri-drag-region="false" onClick={handleCloseConfigModal}>
          <form
            className="desktop-modal desktop-memory-modal desktop-config-modal"
            onSubmit={handleSaveConfig}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="desktop-modal__head">
              <div>
                <h3>模型配置</h3>
                <p className="desktop-config-modal__hint">
                  <img src={circleAlertIcon} alt="" />
                  <span>修改并填写模型信息配置，完成连通性测试后，配置保存才能正式生效。</span>
                </p>
              </div>
              <button className="desktop-modal__close" type="button" aria-label="关闭" onClick={handleCloseConfigModal}>
                ×
              </button>
            </div>

            <div className="desktop-config-tabs" role="tablist" aria-label="模型配置类型">
              <button
                aria-selected={configTab === 'chat'}
                className={configTab === 'chat' ? 'is-active' : ''}
                role="tab"
                type="button"
                onClick={() => setConfigTab('chat')}
              >
                对话模型
              </button>
              <button
                aria-selected={configTab === 'embedding'}
                className={configTab === 'embedding' ? 'is-active' : ''}
                role="tab"
                type="button"
                onClick={() => setConfigTab('embedding')}
              >
                Embedding 模型
              </button>
            </div>

            {loadingConfig ? <div className="desktop-empty-state">配置加载中...</div> : null}

            {configTab === 'chat' ? (
              <div className="desktop-config-panel" role="tabpanel">
                <label className="desktop-field">
                  <span>提供方名称</span>
                  <input disabled={loadingConfig} value={config.provider} onChange={(event) => setConfig({ ...config, provider: event.target.value })} />
                </label>
                <label className="desktop-field">
                  <span>Base URL</span>
                  <input
                    disabled={loadingConfig}
                    placeholder="https://api.deepseek.com"
                    value={config.base_url}
                    onChange={(event) => setConfig({ ...config, base_url: event.target.value })}
                  />
                </label>
                <label className="desktop-field">
                  <span>API Key</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    disabled={loadingConfig}
                    placeholder={hasStoredChatApiKey ? '已保存，留空则不修改' : ''}
                    spellCheck={false}
                    value={configSecretInputValue(config.api_key)}
                    onChange={(event) => setConfig({ ...config, api_key: event.target.value })}
                  />
                </label>
                <label className="desktop-field">
                  <span>模型名称</span>
                  <input disabled={loadingConfig} value={config.model} onChange={(event) => setConfig({ ...config, model: event.target.value })} />
                </label>
              </div>
            ) : (
              <div className="desktop-config-panel" role="tabpanel">
                <label className="desktop-field">
                  <span>Embedding Base URL</span>
                  <input
                    disabled={loadingConfig}
                    placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
                    value={config.embedding_base_url}
                    onChange={(event) => setConfig({ ...config, embedding_base_url: event.target.value })}
                  />
                </label>
                <label className="desktop-field">
                  <span>Embedding API Key</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    disabled={loadingConfig}
                    placeholder={hasStoredEmbeddingApiKey ? '已保存，留空则不修改' : ''}
                    spellCheck={false}
                    value={configSecretInputValue(config.embedding_api_key)}
                    onChange={(event) => setConfig({ ...config, embedding_api_key: event.target.value })}
                  />
                </label>
                <label className="desktop-field">
                  <span>Embedding 模型</span>
                  <input
                    disabled={loadingConfig}
                    placeholder="text-embedding-v4"
                    value={config.embedding_model}
                    onChange={(event) => setConfig({ ...config, embedding_model: event.target.value })}
                  />
                </label>
              </div>
            )}

            <div className="desktop-config-modal__status">
              {statusMessage ? <span className="desktop-status desktop-status--ok">{statusMessage}</span> : null}
              {errorMessage ? <span className="desktop-status desktop-status--error">{errorMessage}</span> : null}
            </div>

            <div className="desktop-modal__footer">
              <button className="desktop-secondary-button" type="button" onClick={handleCloseConfigModal}>
                取消
              </button>
              <button className="desktop-secondary-button" disabled={loadingConfig || !configLoaded || testingConfig || savingConfig} type="button" onClick={() => void handleTestConfig()}>
                {testingConfig ? '测试中...' : '测试连通性'}
              </button>
              <button className="desktop-primary-button" disabled={loadingConfig || !configLoaded || savingConfig || testingConfig} type="submit">
                {savingConfig ? '保存中...' : '保存配置'}
              </button>
            </div>
          </form>
        </div>
      ) : null}

    </div>
  );
}
