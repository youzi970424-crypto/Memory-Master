import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function loadApi(fetch, immediateTimeout = false) {
  const context = {
    exports: {}, fetch, AbortController, Error, TypeError, SyntaxError,
    localStorage: { getItem: () => null },
    window: {
      setTimeout: (callback, ms) => setTimeout(callback, immediateTimeout || ms < 1000 ? 0 : ms),
      clearTimeout,
    },
  };
  vm.runInNewContext(compiled, context);
  return context.exports.api;
}

const chat = (api) => api.chat('居家办公准备什么？', [], []);

test('lost POST response is not retried or misreported as backend not started', async () => {
  let calls = 0;
  const api = loadApi(async () => { calls++; throw new TypeError('Failed to fetch'); });
  await assert.rejects(chat(api), /通信失败.*连接中断/);
  assert.equal(calls, 1);
});

test('read-only health request still retries transient connection failures', async () => {
  let calls = 0;
  const api = loadApi(async () => {
    if (++calls < 3) throw new TypeError('Failed to fetch');
    return { ok: true, json: async () => ({ ok: true }) };
  });
  assert.equal((await api.health()).ok, true);
  assert.equal(calls, 3);
});

test('HTTP 500 preserves backend error and does not retry chat', async () => {
  let calls = 0;
  const api = loadApi(async () => {
    calls++;
    return { ok: false, status: 500, json: async () => ({ error: '本地服务处理请求时发生异常' }) };
  });
  await assert.rejects(chat(api), /本地服务处理请求时发生异常/);
  assert.equal(calls, 1);
});

test('invalid JSON is a response error, not a network error', async () => {
  const api = loadApi(async () => ({ status: 502, json: async () => { throw new SyntaxError('invalid JSON'); } }));
  await assert.rejects(chat(api), /无法解析的响应 \(502\)/);
});

test('timeout explains that processing may still be running', async () => {
  const api = loadApi(async (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    });
  }), true);
  await assert.rejects(chat(api), /请求等待超时.*仍在处理/);
});

test('valid model result passes through unchanged', async () => {
  const result = { answer: { recommendation: '检查网络和设备' }, meta: { generation_mode: 'model' } };
  const api = loadApi(async () => ({ ok: true, json: async () => result }));
  assert.equal(await chat(api), result);
});

test('timeout while reading the response body remains a timeout', async () => {
  const api = loadApi(async () => ({ status: 200, json: async () => {
    const error = new Error('aborted');
    error.name = 'AbortError';
    throw error;
  } }));
  await assert.rejects(chat(api), /请求等待超时/);
});
