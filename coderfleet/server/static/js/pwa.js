'use strict';

// ── PWA 安装提示 ─────────────────────────────────────────────────────────

let _installPrompt = null;

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _installPrompt = e;
  const btn = document.getElementById('pwa-install-btn');
  if (btn) btn.style.display = '';
});

window.addEventListener('appinstalled', () => {
  _installPrompt = null;
  const btn = document.getElementById('pwa-install-btn');
  if (btn) btn.style.display = 'none';
});

async function installPWA() {
  if (!_installPrompt) return;
  _installPrompt.prompt();
  const { outcome } = await _installPrompt.userChoice;
  if (outcome === 'accepted') {
    _installPrompt = null;
    const btn = document.getElementById('pwa-install-btn');
    if (btn) btn.style.display = 'none';
  }
}

// ── Web Push 通知 ────────────────────────────────────────────────────────

function _urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function _getPushSubscription() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return null;
  try {
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  } catch {
    return null;
  }
}

async function subscribePush() {
  if (!('Notification' in window) || !('PushManager' in window)) return false;

  const perm = await Notification.requestPermission();
  if (perm !== 'granted') {
    updateNotifBtn();
    return false;
  }

  try {
    const resp = await fetch(`${API}/api/push/vapid-public-key`);
    if (!resp.ok) return false;
    const { publicKey } = await resp.json();

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: _urlBase64ToUint8Array(publicKey),
      });
    }

    await fetch(`${API}/api/push/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: sub.toJSON() }),
    });

    updateNotifBtn();
    return true;
  } catch (err) {
    console.error('[PWA] Push subscription failed:', err);
    return false;
  }
}

async function unsubscribePush() {
  if (!('serviceWorker' in navigator)) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) { updateNotifBtn(); return; }

    await fetch(`${API}/api/push/unsubscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint }),
    });
    await sub.unsubscribe();
  } catch (err) {
    console.error('[PWA] Push unsubscribe failed:', err);
  }
  updateNotifBtn();
}

async function togglePushNotification() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'denied') {
    alert('通知权限已被浏览器禁止，请在浏览器地址栏的网站设置中手动开启。');
    return;
  }
  const sub = await _getPushSubscription();
  if (sub) {
    await unsubscribePush();
  } else {
    await subscribePush();
  }
}

async function updateNotifBtn() {
  const btn = document.getElementById('pwa-notif-btn');
  if (!btn) return;

  if (!('Notification' in window) || !('PushManager' in window)) {
    btn.style.display = 'none';
    return;
  }

  const perm = Notification.permission;
  const sub = await _getPushSubscription();

  btn.classList.remove('notif-active', 'notif-denied');
  if (perm === 'denied') {
    btn.title = '通知已被禁止（在浏览器设置中解除）';
    btn.classList.add('notif-denied');
  } else if (sub) {
    btn.title = '推送通知已开启（点击关闭）';
    btn.classList.add('notif-active');
  } else {
    btn.title = '开启任务完成推送通知';
  }
}

document.addEventListener('DOMContentLoaded', () => updateNotifBtn());
