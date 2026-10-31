import { spawnSync } from 'node:child_process';
if (process.platform === 'win32') {
  if (process.arch !== 'x64') throw new Error('当前 Windows 安装包仅支持 x64；请使用 x64 Node.js 和 x64 Rust 工具链。');
  const result = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'scripts/prepare-windows.ps1'], { stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
