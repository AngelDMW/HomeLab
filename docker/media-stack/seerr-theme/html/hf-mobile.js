/* HomeFlix · parche móvil idempotente para Seerr (Next.js).
   Se inyecta con defer desde nginx. Nunca duplica nodos ni lanza errores. */
(function () {
  'use strict';
  if (window.__HF_MOBILE__) return;
  window.__HF_MOBILE__ = true;

  var win = window;
  var doc = document;
  var root = doc.documentElement;
  var observer = null;
  var timer = null;
  var mutations = 0;

  function one(selector) {
    return doc.head ? doc.head.querySelector(selector) : null;
  }
  function all(selector) {
    return doc.head ? doc.head.querySelectorAll(selector) : [];
  }
  function setMeta(name, content) {
    var el = one('meta[name="' + name + '"]');
    if (!el) {
      el = doc.createElement('meta');
      el.setAttribute('name', name);
      doc.head.appendChild(el);
    }
    if (el.getAttribute('content') !== content) el.setAttribute('content', content);
  }

  function patch() {
    if (!doc.head) return;
    try {
      root.classList.add('hf-mobile');
      if (/iP(hone|od|ad)/.test(navigator.platform) ||
          (navigator.maxTouchPoints > 1 && /Macintosh/.test(navigator.platform))) {
        root.classList.add('hf-ios');
      }
      if (win.matchMedia && win.matchMedia('(hover: none), (pointer: coarse)').matches) {
        root.classList.add('hf-touch');
      }

      var vp = one('meta[name="viewport"]');
      if (!vp) {
        vp = doc.createElement('meta');
        vp.setAttribute('name', 'viewport');
        doc.head.insertBefore(vp, doc.head.firstChild);
      }
      var VPC = 'width=device-width, initial-scale=1, viewport-fit=cover';
      if (vp.getAttribute('content') !== VPC) vp.setAttribute('content', VPC);

      var themes = all('meta[name="theme-color"]');
      for (var i = themes.length - 1; i > 0; i--) {
        themes[i].parentNode.removeChild(themes[i]);
      }
      setMeta('theme-color', '#0b0b0f');

      setMeta('apple-mobile-web-app-capable', 'yes');
      setMeta('apple-mobile-web-app-status-bar-style', 'black-translucent');
      setMeta('apple-mobile-web-app-title', 'HomeFlix');
      setMeta('mobile-web-app-capable', 'yes');

      var mans = all('link[rel~="manifest"]');
      for (var j = mans.length - 1; j > 0; j--) {
        mans[j].parentNode.removeChild(mans[j]);
      }
      var man = mans[0];
      if (!man) {
        man = doc.createElement('link');
        man.setAttribute('rel', 'manifest');
        doc.head.appendChild(man);
      }
      if (man.getAttribute('href') !== '/manifest.json') {
        man.setAttribute('href', '/manifest.json');
      }

      root.setAttribute('data-hf-mobile', 'ok');
    } catch (err) {
      /* Nunca propagar errores al SPA */
    }
  }

  function run() {
    if (observer) observer.disconnect();
    patch();
    if (observer && doc.head) observer.observe(doc.head, { childList: true });
  }

  function schedule() {
    if (timer) win.clearTimeout(timer);
    timer = win.setTimeout(function () {
      timer = null;
      mutations++;
      if (mutations > 100) {
        if (observer) observer.disconnect();
        return;
      }
      run();
    }, 140);
  }

  function init() {
    patch();
    if (win.MutationObserver && doc.head) {
      observer = new win.MutationObserver(schedule);
      observer.observe(doc.head, { childList: true });
    }
    win.addEventListener('popstate', schedule, { passive: true });
    win.addEventListener('pageshow', schedule, { passive: true });
  }

  if (doc.readyState === 'loading') {
    doc.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
