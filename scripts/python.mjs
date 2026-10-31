import { spawn, spawnSync } from 'node:child_process';

export function startPython(args, options = {}) {
  const candidates = process.env.MEMORY_MASTER_PYTHON
    ? [[process.env.MEMORY_MASTER_PYTHON]]
    : process.platform === 'win32'
      ? [['py', '-3.12'], ['python'], ['py', '-3']]
      : [['python3.12'], ['python3']];
  for (const [command, ...prefix] of candidates) {
    const probe = spawnSync(command, [...prefix, '-c', 'import sys; sys.exit(sys.version_info < (3, 12))'], { windowsHide: true });
    if (probe.status === 0) {
      return spawn(command, [...prefix, ...args], { stdio: 'inherit', windowsHide: true, ...options });
    }
  }
  throw new Error('需要 Python 3.12 或更新版本；可通过 MEMORY_MASTER_PYTHON 指定解释器完整路径。');
}
