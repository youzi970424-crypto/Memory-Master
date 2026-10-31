import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { useEffect, useRef, useState } from 'react';
import logo from './assets/home/quick-entry-logo.png';

export type QuickPreferences = { visible: boolean; hide_notice_seen: boolean };
const nativeHover = /Mac/i.test(navigator.platform);
export const quickShortcut = /Mac/i.test(navigator.platform) ? 'Command + Shift + M' : 'Ctrl + Shift + M';

export function QuickEntrySetting() {
  const [visible, setVisible] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const subscription = listen<QuickPreferences>('quick-preferences-changed', ({ payload }) => setVisible(payload.visible));
    void invoke<QuickPreferences>('quick_preferences').then(p => setVisible(p.visible)).catch(e => setError(String(e)));
    return () => { void subscription.then(stop => stop()); };
  }, []);
  return <>
    <button className="desktop-settings-menu__item desktop-settings-menu__toggle" type="button" role="menuitemcheckbox" aria-checked={visible === true} disabled={visible === null}
      onClick={() => { void invoke('set_quick_entry_visible', { visible: !visible, confirmed: true }).catch(e => setError(String(e))); }}>
      <span>快捷录入窗口</span>
      <span className="desktop-settings-menu__switch" aria-hidden="true" />
    </button>
    {error && <span role="alert">{error}</span>}
  </>;
}

export default function QuickEntry() {
  const [notice, setNotice] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [leftward, setLeftward] = useState(false);
  const [error, setError] = useState('');
  const press = useRef<{ x: number; y: number } | null>(null);
  const dragged = useRef(false);
  const dragQueue = useRef(Promise.resolve());
  function drag(phase: string) {
    dragQueue.current = dragQueue.current.then(() => invoke<void>('quick_entry_drag', { phase })).catch(e => setError(String(e)));
  }
  function finishDrag() {
    if (!press.current) return;
    press.current = null;
    drag(dragged.current ? 'end' : 'cancel');
    if (dragged.current) setExpanded(false);
  }
  useEffect(() => {
    document.body.classList.add('is-quick-entry');
    const subscription = listen('quick-hide-notice', () => setNotice(true));
    const preferencesSubscription = listen<QuickPreferences>('quick-preferences-changed', ({ payload }) => {
      if (payload.visible) return;
      setNotice(false);
      setExpanded(false);
      void invoke('quick_entry_notice', { expanded: false }).catch(e => setError(String(e)));
    });
    const dragSubscription = listen('quick-drag-ended', () => { press.current = null; setExpanded(false); });
    return () => { document.body.classList.remove('is-quick-entry'); void subscription.then(stop => stop()); void preferencesSubscription.then(stop => stop()); void dragSubscription.then(stop => stop()); };
  }, []);
  async function expand(value: boolean) {
    if (notice || press.current) return;
    try {
      const direction = await invoke<boolean>('quick_entry_hover', { expanded: value });
      if (value) setLeftward(direction);
      setExpanded(value);
    }
    catch (e) { setError(String(e)); }
  }
  useEffect(() => {
    if (!nativeHover || notice) return;
    let stopped = false;
    let previous: boolean | null = null;
    let timer: ReturnType<typeof setTimeout>;
    async function checkPointer() {
      try {
        const inside = await invoke<boolean>('quick_entry_pointer_inside');
        if (stopped) return;
        if (inside !== previous) {
          previous = inside;
          await expand(inside);
        }
      } catch (e) { if (!stopped) setError(String(e)); }
      if (!stopped) timer = setTimeout(() => void checkPointer(), 100);
    }
    void checkPointer();
    return () => { stopped = true; clearTimeout(timer); };
  }, [notice]);
  async function open() {
    if (dragged.current) return;
    await dragQueue.current;
    await expand(false);
    try { await invoke('show_quick_add_window'); }
    catch (e) { setError(String(e)); }
  }
  async function hide() {
    try {
      if (!notice) {
        await invoke('quick_entry_notice', { expanded: true });
        setNotice(true);
        return;
      }
      await invoke('set_quick_entry_visible', { visible: false, confirmed: notice });
      await invoke('quick_entry_notice', { expanded: false });
      setNotice(false);
      setExpanded(false);
    } catch (e) { setError(String(e)); }
  }
  return <div className={`quick-entry ${notice ? 'quick-entry--notice' : ''} ${expanded ? 'quick-entry--expanded' : ''} ${leftward ? 'quick-entry--leftward' : ''}`}
    onMouseEnter={() => { if (!nativeHover) void expand(true); }} onMouseLeave={() => { if (!nativeHover) void expand(false); }}>
    {notice ? <>
      <strong>桌面入口已隐藏</strong>
      <p>仍可按 {quickShortcut} 快速记录，也可在「设置 → 快捷录入窗口」中重新开启。</p>
      <button type="button" onClick={() => void hide()}>知道了</button>
    </> : <>
      <div className="quick-entry__launcher">
      <button className="quick-entry__open" aria-label="快速记忆" aria-describedby={expanded ? 'quick-entry-tooltip' : undefined}
        onPointerDown={e => { if (e.button !== 0) return; press.current = { x: e.screenX, y: e.screenY }; dragged.current = false; e.currentTarget.setPointerCapture(e.pointerId); drag('start'); }}
        onPointerMove={e => {
          if (!press.current || (!dragged.current && Math.hypot(e.screenX - press.current.x, e.screenY - press.current.y) < 5)) return;
          dragged.current = true;
          drag('move');
        }}
        onPointerUp={finishDrag} onPointerCancel={finishDrag} onLostPointerCapture={finishDrag}
        onClick={() => void open()}>
        <img src={logo} alt="" />
      </button>
      {expanded && <button className="quick-entry__hide" aria-label="隐藏快捷录入入口" title="隐藏桌面入口" onClick={() => void hide()}>×</button>}
      </div>
      {expanded && <span className="quick-entry__tooltip" id="quick-entry-tooltip" role="tooltip">快速记忆</span>}
    </>}
    {error && <span role="alert">{error}</span>}
  </div>;
}
