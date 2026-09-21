(() => {
  'use strict';
  // randomUUID: Chrome 92+ and secure contexts only (absent on plain-HTTP LAN addresses).
  if (window.crypto && !crypto.randomUUID && crypto.getRandomValues) {
    crypto.randomUUID = () => {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = bytes[6] & 15 | 64;
      bytes[8] = bytes[8] & 63 | 128;
      const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };
  }
  // AbortSignal.timeout: Chrome 103+.
  if (window.AbortSignal && window.AbortController && !AbortSignal.timeout) {
    AbortSignal.timeout = ms => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(new DOMException('signal timed out', 'TimeoutError')), ms);
      return controller.signal;
    };
  }
})();
