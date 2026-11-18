/*
 * Lógica de unirse.html (sin dependencias). En el navegador define `window.PadelPiqueJoin`; en Node, `module.exports`
 * (así se prueba en web/tests/unirse.test.mjs).
 *
 * Contrato (docs/contrato-api.md §5):
 *   - Código: 6 caracteres de ABCDEFGHJKLMNPQRSTUVWXYZ23456789 (sin O, I, 0, 1).
 *   - Enlace web:  https://borjeta.github.io/padelpique/unirse.html?c=ABC234
 *   - Enlace app:  padelpique://join/ABC234
 * La normalización y la validación replican apps/mobile/src/domain/invite.ts (mayúsculas, sin espacios ni guiones;
 * la `O` NO se convierte en `0`: si no está en el alfabeto, el código no es válido).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PadelPiqueJoin = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ===== CAMBIAR AL PUBLICAR LA APP =====================================================================
   * Enlace de la ficha de la App Store. Mientras no empiece por https://apps.apple.com/ el botón de descarga
   * permanece oculto (así nunca se muestra un enlace roto).
   * ===================================================================================================== */
  var APP_STORE_URL = '[CONFIRMAR: https://apps.apple.com/app/idXXXXXXXXXX]';

  var ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  var CODE_LENGTH = 6;
  var APP_SCHEME = 'padelpique';
  var CODE_RE = new RegExp('^[' + ALPHABET + ']{' + CODE_LENGTH + '}$');
  var AUTO_OPEN_DELAY_MS = 300;

  /** Mayúsculas y sin espacios ni guiones. No sustituye letras. */
  function normalizeCode(input) {
    return String(input == null ? '' : input).replace(/[\s-]+/g, '').toUpperCase();
  }

  /** Código normalizado si es válido; `null` si no. */
  function parseCode(input) {
    var code = normalizeCode(input);
    return CODE_RE.test(code) ? code : null;
  }

  function safeDecode(value) {
    try {
      return decodeURIComponent(value);
    } catch (e) {
      return value;
    }
  }

  /** Valor de un parámetro de la query (`?a=1&b=2`), el primero que coincida; `null` si no está. */
  function queryParam(search, name) {
    var pairs = String(search || '').replace(/^\?/, '').split('&');
    for (var i = 0; i < pairs.length; i++) {
      var pair = pairs[i];
      var eq = pair.indexOf('=');
      if (eq === -1) continue;
      if (safeDecode(pair.slice(0, eq)) === name) return safeDecode(pair.slice(eq + 1));
    }
    return null;
  }

  /** Código válido de `?c=CODIGO`, o `null`. */
  function codeFromSearch(search) {
    var raw = queryParam(search, 'c');
    return raw === null ? null : parseCode(raw);
  }

  /** `padelpique://join/ABC234` */
  function appUrl(code) {
    return APP_SCHEME + '://join/' + encodeURIComponent(code);
  }

  function isStoreConfigured(url) {
    return /^https:\/\/apps\.apple\.com\//.test(String(url || ''));
  }

  /** `?lang=en|es` manda; si no, el idioma del navegador (inglés si empieza por "en"; si no, español). */
  function detectLang(search, navigatorLanguage) {
    var forced = queryParam(search, 'lang');
    if (forced === 'en' || forced === 'es') return forced;
    return /^en/i.test(String(navigatorLanguage || '')) ? 'en' : 'es';
  }

  function isMobile(userAgent) {
    return /iPhone|iPad|iPod|Android/i.test(String(userAgent || ''));
  }

  /** Monta la página. `env` permite inyectar document/location/navigator/setTimeout (pruebas en Node). */
  function init(env) {
    env = env || {};
    var doc = env.document || document;
    var loc = env.location || location;
    var nav = env.navigator || navigator;
    var wait = env.setTimeout || setTimeout;
    var $ = function (id) {
      return doc.getElementById(id);
    };

    var lang = detectLang(loc.search, nav.language);
    var setLang = function (value) {
      doc.documentElement.setAttribute('lang', value);
      doc.documentElement.setAttribute('data-lang', value);
    };
    setLang(lang);
    $('lang-es').addEventListener('click', function () { setLang('es'); });
    $('lang-en').addEventListener('click', function () { setLang('en'); });

    var code = codeFromSearch(loc.search);
    var result = { code: code, lang: lang, appUrl: null, autoOpen: false, storeShown: false };

    if (!code) {
      $('invalid').hidden = false;
      $('valid').hidden = true;
      return result;
    }

    var url = appUrl(code);
    result.appUrl = url;
    $('invalid').hidden = true;
    $('valid').hidden = false;
    // textContent (nunca innerHTML): el código ya está validado, pero así no hay nada que inyectar.
    $('code').textContent = code.slice(0, 3) + ' ' + code.slice(3);
    $('code').setAttribute('aria-label', code.split('').join(' '));
    $('open-app').setAttribute('href', url);

    if (isStoreConfigured(APP_STORE_URL)) {
      $('store').setAttribute('href', APP_STORE_URL);
      $('store').hidden = false;
      result.storeShown = true;
    }

    $('copy').addEventListener('click', function () {
      var button = $('copy');
      var done = function () {
        button.classList.add('copied');
        wait(function () { button.classList.remove('copied'); }, 2000);
      };
      try {
        if (nav.clipboard && nav.clipboard.writeText) nav.clipboard.writeText(code).then(done, function () {});
      } catch (e) {
        /* sin portapapeles: el código es seleccionable a mano */
      }
    });

    // En móvil se intenta abrir la app directamente; si no está instalada, la página sigue mostrando el código.
    if (isMobile(nav.userAgent)) {
      result.autoOpen = true;
      wait(function () { loc.href = url; }, AUTO_OPEN_DELAY_MS);
    }
    return result;
  }

  return {
    ALPHABET: ALPHABET,
    APP_SCHEME: APP_SCHEME,
    APP_STORE_URL: APP_STORE_URL,
    normalizeCode: normalizeCode,
    parseCode: parseCode,
    queryParam: queryParam,
    codeFromSearch: codeFromSearch,
    appUrl: appUrl,
    isStoreConfigured: isStoreConfigured,
    detectLang: detectLang,
    isMobile: isMobile,
    init: init,
  };
});
