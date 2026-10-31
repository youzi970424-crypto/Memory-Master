export type MemoryType = '经验型' | '结论型' | '方法型';

export type MemoryCard = {
  id: string;
  title: string;
  content: string;
  type: MemoryType;
  source: string;
  keywords: string[];
  project: string;
  workspace_id: string;
  project_id: string;
  snapshot_id: string;
  folder_id: string | null;
  visibility: 'visible' | 'hidden';
  created_at: string;
  updated_at: string;
  event_at: string | null;
  available_at: string | null;
  valid_until: string | null;
  last_used_at: string | null;
  citations: number;
  weight: number;
  source_weight: number;
  quality_weight: number;
  dynamic_weight: number;
};

export type MemoryFolder = {
  id: string;
  name: string;
  is_default: boolean;
  memory_count: number;
};

export type ParsedMemoryDraft = {
  title: string;
  content: string;
  keywords: string[];
  folder: string;
};

export type WeeklyTodo = {
  text: string;
  memory_id: string;
  memory_title: string;
  source: string;
};

export type ApiConfig = {
  provider: string;
  base_url: string;
  api_key: string;
  model: string;
  embedding_base_url: string;
  embedding_api_key: string;
  embedding_model: string;
  enabled: boolean;
};

export type AuthUser = {
  id: string;
  account: string;
  display_name: string;
  created_at: string;
};

export type AuthSession = {
  token: string;
  user: AuthUser;
};

export type ChatResult = {
  answer: {
    summary: string;
    recommendation: string;
    reasoning_points: { text: string; memory_ids: string[] }[];
    action_items: { text: string; priority: 'high' | 'medium' | 'low' }[];
    uncertainties: { text: string; memory_ids: string[] }[];
  };
  memory_usage: {
    selected_memory_ids: string[];
    manual_memory_ids: string[];
    auto_recalled_memory_ids: string[];
    dropped_memory_ids: string[];
    citation_count: number;
    primary_citation_ids: string[];
    secondary_citation_ids: string[];
    recalled_memories: (
      MemoryCard & {
        selection: 'manual' | 'auto';
        citation_priority: 'primary' | 'secondary';
        score: number;
      }
    )[];
    weekly_todos: WeeklyTodo[];
    citations: MemoryCard[];
  };
  memory_capture: {
    should_save: boolean;
    suggested_memory_card: { title: string; content: string; keywords: string[]; source: string };
  };
  meta: {
    retrieval_mode: string;
    generation_mode: string;
    model: string;
    retrieved_count: number;
    used_count: number;
    turn_type?: string;
    retrieval_scope?: { workspace_id?: string; project_id?: string; snapshot_id?: string; as_of?: string };
    turn_classification?: {
      turn_type: string;
      confidence: number;
      reason: string;
    };
    error?: string;
    error_code?: string;
    warnings?: string[];
    harness?: {
      request_id: string;
      version: string;
      elapsed_ms: number;
      provider_calls: number;
      total_tokens: number | null;
      usage_complete: boolean;
      trace_persisted: boolean;
    };
  };
};

const API_BASE = 'http://127.0.0.1:8765/api';
const AUTH_STORAGE_KEY = 'memory-master.auth-session.v1';
const LOCAL_REQUEST_TIMEOUT_MS = 8000;
// Leave transport headroom above the server's 90-second Harness budget.
const CHAT_REQUEST_TIMEOUT_MS = 120000;

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function authToken() {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return '';
    const parsed = JSON.parse(raw) as { token?: unknown };
    return typeof parsed.token === 'string' ? parsed.token : '';
  } catch {
    return '';
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = LOCAL_REQUEST_TIMEOUT_MS,
): Promise<T> {
  // Retrying POST after a lost response can duplicate work or writes.
  const maxAttempts = !init?.method || init.method === 'GET' ? 3 : 1;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const token = authToken();
      const headers = {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers as Record<string, string> | undefined),
      };
      const response = await fetch(`${API_BASE}${path}`, {
        ...init,
        signal: controller.signal,
        headers,
      });
      const payload = await response.json().catch((error: unknown) => {
        if (error instanceof SyntaxError) {
          throw new Error(`本地服务返回了无法解析的响应 (${response.status})`);
        }
        throw error;
      });
      if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
      return payload as T;
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        throw new Error('请求等待超时，后端或模型可能仍在处理，请稍后重试');
      }
      if (error instanceof TypeError) {
        if (attempt === maxAttempts) {
          throw new Error('与本地服务通信失败，可能是服务未启动或连接中断，请检查服务状态和后端日志');
        }
        await sleep(150 * attempt);
        continue;
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  throw new Error('本地服务暂时不可用');
}

export const api = {
  health: () => request<{ ok: boolean }>('/health'),
  register: (account: string, password: string, displayName = '') =>
    request<AuthSession>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ account, password, display_name: displayName }),
    }),
  login: (account: string, password: string) =>
    request<AuthSession>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ account, password }),
    }),
  currentUser: (token: string) =>
    request<{ user: AuthUser }>('/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    }),
  logout: (token: string) =>
    request<{ ok: boolean }>('/auth/logout', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({ token }),
    }),
  listMemories: (query = '') => request<{ items: MemoryCard[] }>(`/memories${query ? `?query=${encodeURIComponent(query)}` : ''}`),
  listFolders: () => request<{ items: MemoryFolder[] }>('/folders'),
  createFolder: (name: string) =>
    request<MemoryFolder>('/folders', { method: 'POST', body: JSON.stringify({ name }) }),
  updateFolder: (id: string, name: string) =>
    request<MemoryFolder>(`/folders/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    }),
  deleteFolder: (id: string) =>
    request<{ deleted: boolean; id: string; unassigned_memory_count: number }>(
      `/folders/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    ),
  createMemory: (memory: Partial<MemoryCard>) =>
    request<MemoryCard>('/memories', { method: 'POST', body: JSON.stringify(memory) }),
  updateMemory: (id: string, memory: Partial<MemoryCard>) =>
    request<MemoryCard>(`/memories/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(memory) }),
  deleteMemory: (id: string) =>
    request<{ deleted: boolean }>(`/memories/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  chat: (query: string, manualMemoryIds: string[], history: { role: 'user' | 'assistant'; text: string }[], previousScope?: ChatResult['meta']['retrieval_scope']) =>
    request<ChatResult>('/chat', {
      method: 'POST',
      body: JSON.stringify({ query, manual_memory_ids: manualMemoryIds, conversation_history: history, previous_retrieval_scope: previousScope }),
    }, CHAT_REQUEST_TIMEOUT_MS),
  parseMemory: (content: string, title = '', folders: string[] = []) =>
    request<{ draft: ParsedMemoryDraft; meta: { generation_mode: string; model: string } }>('/memories/parse', {
      method: 'POST',
      body: JSON.stringify({ content, title, folders }),
    }, CHAT_REQUEST_TIMEOUT_MS),
  getConfig: () => request<ApiConfig>('/config'),
  saveConfig: (config: ApiConfig) => request<ApiConfig>('/config', { method: 'POST', body: JSON.stringify(config) }),
  testConfig: (target: 'chat' | 'embedding' | 'all' = 'all', config?: ApiConfig) =>
    request<{ success: boolean; provider: string; model: string }>('/config/test', {
      method: 'POST',
      body: JSON.stringify({ target, config }),
    }),
};
