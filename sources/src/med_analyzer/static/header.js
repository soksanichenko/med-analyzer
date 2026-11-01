// Language picker: remember the choice in a cookie that the server reads, then
// reload the page so it is rendered in that language.
(() => {
  'use strict';

  const select = document.getElementById('lang-select');
  if (!select) {
    return;
  }
  select.addEventListener('change', () => {
    const secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie =
      `lang=${encodeURIComponent(select.value)}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
    window.location.reload();
  });
})();
