/**
 * Marshal Review — compact light theme.
 * Insights black was .bg-tertiary-subtle → dark --bs-tertiary-bg-subtle;
 * Datashare stores theme on body + localStorage key "data-bs-theme".
 */
(function () {
  var STYLE_ID = 'marshal-theme';
  var BG = '#f4f4f1';
  var PANEL = '#ffffff';
  var FG = '#1a1a1a';
  var MUTED = '#6b6b66';
  var BORDER = '#ddd9d2';
  var HOVER = '#ebeae4';
  var ACCENT = '#1f4b3a';
  var ACCENT_SOFT = '#e8efec';

  var css = [
    'html{color-scheme:light only!important}',
    ':root,[data-bs-theme=light],[data-bs-theme=dark],html,body,html[data-bs-theme=dark],body[data-bs-theme=dark]{',
    '  --marshal-bg:' + BG + ';--marshal-panel:' + PANEL + ';--marshal-fg:' + FG + ';',
    '  --bs-body-bg:' + BG + '!important;--bs-body-color:' + FG + '!important;',
    '  --bs-body-font-size:.8125rem!important;--bs-body-line-height:1.35!important;',
    '  --bs-body-font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif!important;',
    '  --bs-primary:' + ACCENT + '!important;--bs-primary-rgb:31,75,58!important;',
    '  --bs-pink:' + ACCENT + '!important;--bs-pink-rgb:31,75,58!important;',
    '  --bs-link-color:' + ACCENT + '!important;--bs-border-color:' + BORDER + '!important;',
    '  --bs-secondary-bg:#eceae4!important;--bs-tertiary-bg:' + HOVER + '!important;',
    '  --bs-secondary-bg-subtle:#eceae4!important;',
    '  --bs-tertiary-bg-subtle:' + BG + '!important;', /* Insights / app-wait */
    '  --bs-dark:#3d3d3a!important;--bs-dark-rgb:61,61,58!important;',
    '  --bs-black-rgb:31,75,58!important;',
    '  color-scheme:light only!important',
    '}',
    'html,body,#app{background:' + BG + '!important;color:' + FG + '!important;font-size:.8125rem!important;line-height:1.35!important}',

    /* CRITICAL: Insights panel */
    '.bg-tertiary-subtle,.bg-body-tertiary,.bg-secondary-subtle,.app-wait,.app-wait.bg-tertiary-subtle,',
    '.project-view-overview .bg-tertiary-subtle,[class*=bg-tertiary-subtle]{',
    '  background:' + BG + '!important;background-color:' + BG + '!important;background-image:none!important;color:' + FG + '!important',
    '}',

    /* compact type */
    'h1,.h1,.jumbotron>h1,.jumbotron>.h1{font-family:ui-sans-serif,system-ui,sans-serif!important;font-size:1.25rem!important;font-weight:600!important}',
    'h2,.h2{font-size:1.1rem!important;font-weight:600!important}',
    'h3,.h3,h4,.h4{font-size:1rem!important;font-weight:600!important}',
    '.project-card-label{font-size:.9375rem!important;font-weight:600!important;margin:0!important}',
    '.project-jumbotron__header__title{font-size:1.15rem!important;font-weight:600!important}',
    '.project-thumbnail--colorized .project-thumbnail__caption{font-size:28cqw!important;font-weight:600!important;color:' + ACCENT + '!important}',

    /* compact spacing */
    '.page-container,.page-container--deck{gap:.75rem!important;padding:.75rem 1rem!important}',
    '.app-header{padding:.4rem .75rem!important;border-bottom:1px solid ' + BORDER + '!important}',
    '.app-sidebar-section-title,.app-sidebar-section-entry__link,.nav-link{font-size:.75rem!important}',
    '.app-sidebar-section-entry,.nav-link{padding:.25rem .5rem!important}',
    '.btn{font-size:.75rem!important;padding:.28rem .6rem!important;border-radius:6px!important;box-shadow:none!important;font-weight:500!important}',
    '.btn-sm{font-size:.7rem!important;padding:.2rem .45rem!important}',
    '.form-control,.form-select,.form-control-search,.input-group-text{font-size:.8125rem!important;padding:.35rem .55rem!important;border-radius:6px!important}',
    '.card,.document-card,.document-card-grid,.batch-search-card,.list-group-item{padding:.55rem .7rem!important;border-radius:6px!important}',
    '.badge,.b-form-tag{font-size:.65rem!important;padding:.15rem .4rem!important;font-weight:600!important}',
    '.table>:not(caption)>*>*{padding:.35rem .5rem!important;font-size:.8125rem!important}',

    /* black chrome killers */
    '.textured-deck,[class*=textured-deck],.page-container--deck,.jumbotron{',
    '  background:' + BG + '!important;background-color:' + BG + '!important;background-image:none!important;color:' + FG + '!important',
    '}',
    '.textured-deck .text-white,.textured-deck .text-light,[class*=textured-deck] .text-white,[class*=textured-deck] .text-light{color:' + FG + '!important}',
    '.bg-dark,.bg-black,.text-bg-dark,.text-bg-black,.navbar-dark,.list-group-item-dark,.table-dark{',
    '  background:' + PANEL + '!important;background-color:' + PANEL + '!important;color:' + FG + '!important;border-color:' + BORDER + '!important',
    '}',
    '.btn-dark,.btn-outline-dark,.btn-outline-light,.btn-light,.btn-secondary,.btn-outline-primary,.btn-outline-pink{',
    '  background:' + PANEL + '!important;border-color:' + BORDER + '!important;color:' + FG + '!important',
    '}',
    '.btn-dark:hover,.btn-outline-dark:hover,.btn-outline-light:hover,.list-group-item:hover,.list-group-item-action:hover,',
    '.dropdown-item:hover,.dropdown-item:focus,.dropdown-item.active,.page-link:hover,.nav-link:hover{',
    '  background:' + HOVER + '!important;color:' + FG + '!important;border-color:' + BORDER + '!important',
    '}',
    '.list-group-item.active,.nav-pills .nav-link.active,.nav-link.active{',
    '  background:' + ACCENT_SOFT + '!important;color:' + ACCENT + '!important;border-color:' + ACCENT + '!important',
    '}',

    /* projects */
    '.project-card-thumbnail,.project-thumbnail,.project-row-thumbnail,.project-card,.project-button,.card{',
    '  background:' + PANEL + '!important;color:' + FG + '!important;border-color:' + BORDER + '!important;box-shadow:none!important',
    '}',
    '.project-thumbnail--colorized{background:' + ACCENT_SOFT + '!important}',
    '.project-thumbnail__check{background:rgba(31,75,58,.35)!important;color:#fff!important;backdrop-filter:none!important}',

    /* shell */
    '.app-header,.app-sidebar,.filters-panel,.modal-content,.dropdown-menu,.offcanvas,',
    '.form-control,.form-select,.form-control-search,.page-header-toolbar__search{',
    '  background:' + PANEL + '!important;color:' + FG + '!important;border-color:' + BORDER + '!important;box-shadow:none!important',
    '}',
    '.app-header__brand img,.app-footer,.app-footer__icij,.app-sidebar-footer-logo{display:none!important}',
    '.app-header__brand,.app-header__brand *{color:' + ACCENT + '!important;font-size:.7rem!important;font-weight:600!important;letter-spacing:.04em!important;text-transform:uppercase!important}',
    '.app-sidebar-section-entry--active,.app-sidebar-section-entry:hover{background:' + HOVER + '!important;color:' + ACCENT + '!important}',

    '.btn-primary,.btn-pink,.btn-icij,.btn-action{background:' + ACCENT + '!important;border-color:' + ACCENT + '!important;color:#fff!important}',
    '.bg-pink,.badge.bg-pink,.badge.bg-primary,.badge.text-bg-primary,.progress-bar,.splashscreen__progress-bar{background:' + ACCENT + '!important;color:#fff!important}',
    'a,.link-primary,.nav-link{color:' + ACCENT + '!important}',
    '.text-muted,.text-secondary,.text-pink{color:' + MUTED + '!important}',
    'mark,.mark,.search-highlight{background:#dfece6!important}',
    '.form-control:focus,.form-select:focus{border-color:' + ACCENT + '!important;box-shadow:0 0 0 2px rgba(31,75,58,.12)!important}',
    '.form-check-input:checked{background-color:' + ACCENT + '!important;border-color:' + ACCENT + '!important}',
    '.splashscreen{background:' + BG + '!important;color:' + FG + '!important}',
    '.splashscreen__panel__logo{opacity:.3!important;filter:grayscale(1)!important;animation:none!important;max-height:40px!important}'
  ].join('\n');

  function forceLightTheme() {
    var root = document.documentElement;
    var body = document.body;
    root.setAttribute('data-bs-theme', 'light');
    root.style.colorScheme = 'light only';
    if (body) {
      body.setAttribute('data-bs-theme', 'light');
      body.style.colorScheme = 'light only';
    }
    try {
      localStorage.setItem('data-bs-theme', 'light'); // Datashare key
    } catch (e) {}
  }

  function upsertStyle() {
    var el = document.getElementById(STYLE_ID);
    if (!el) {
      el = document.createElement('style');
      el.id = STYLE_ID;
      (document.head || document.documentElement).appendChild(el);
    }
    el.textContent = css;
  }

  function boot() {
    upsertStyle();
    forceLightTheme();
  }

  boot();
  document.addEventListener('datashare:ready', boot);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  }

  // Only watch theme attribute flips from Datashare — not the whole DOM tree
  var pending = false;
  var obs = new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var m = mutations[i];
      if (m.type === 'attributes' && m.attributeName === 'data-bs-theme') {
        var t = (m.target.getAttribute && m.target.getAttribute('data-bs-theme')) || '';
        if (t && t !== 'light') {
          if (pending) return;
          pending = true;
          requestAnimationFrame(function () {
            pending = false;
            forceLightTheme();
          });
        }
      }
    }
  });
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-bs-theme'] });
  if (document.body) {
    obs.observe(document.body, { attributes: true, attributeFilter: ['data-bs-theme'] });
  } else {
    document.addEventListener(
      'DOMContentLoaded',
      function () {
        if (document.body) obs.observe(document.body, { attributes: true, attributeFilter: ['data-bs-theme'] });
      },
      { once: true }
    );
  }

  // After Vue boots it may set theme again — one delayed reinforce
  setTimeout(forceLightTheme, 500);
  setTimeout(forceLightTheme, 2000);
})();
