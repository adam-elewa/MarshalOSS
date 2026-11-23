/**
 * Marshal Entities — project-wide NamedEntity browser.
 * Replaces Datashare's NLP task list at #/tasks/entities with:
 *   left: searchable entity list by category
 *   right: documents that mention the selected entity
 */
(function () {
  var STYLE_ID = 'marshal-entities-style';
  var ROOT_ID = 'marshal-entities-root';
  var coreRef = null;
  var mounted = false;
  var state = {
    project: null,
    projects: [],
    category: 'PERSON',
    q: '',
    entitiesAll: [],
    entities: [],
    selected: null,
    docs: [],
    loadingList: false,
    loadingDocs: false,
    error: null,
  };

  var SKIP_PROJECTS = { 'local-datashare': true };

  var CATEGORIES = [
    { id: 'PERSON', label: 'Person' },
    { id: 'ORGANIZATION', label: 'Organization' },
    { id: 'LOCATION', label: 'Location' },
    { id: 'EMAIL', label: 'Email' },
    { id: 'DATE', label: 'Date' },
    { id: 'MONEY', label: 'Money' },
    { id: 'NUMBER', label: 'Number' },
  ];

  var css = [
    '#' + ROOT_ID + '{display:flex;flex-direction:column;height:calc(100vh - 4.5rem);min-height:420px;background:var(--marshal-bg,#f4f4f1);color:var(--marshal-fg,#1a1a1a);font-size:.8125rem}',
    '#' + ROOT_ID + ' .me-header{display:flex;align-items:center;justify-content:space-between;gap:.75rem;padding:.65rem 1rem;border-bottom:1px solid #ddd9d2;background:#fff}',
    '#' + ROOT_ID + ' .me-header h1{margin:0;font-size:1.05rem;font-weight:600}',
    '#' + ROOT_ID + ' .me-header .me-meta{color:#6b6b66;font-size:.75rem;display:flex;align-items:center;gap:.5rem;margin-top:.2rem}',
    '#' + ROOT_ID + ' .me-project{border:1px solid #ddd9d2;border-radius:6px;padding:.2rem .45rem;font-size:.75rem;background:#fff}',
    '#' + ROOT_ID + ' .me-body{display:flex;flex:1;min-height:0}',
    '#' + ROOT_ID + ' .me-left{width:min(420px,42%);border-right:1px solid #ddd9d2;display:flex;flex-direction:column;background:#fff;min-width:260px}',
    '#' + ROOT_ID + ' .me-right{flex:1;display:flex;flex-direction:column;min-width:0;background:#f4f4f1}',
    '#' + ROOT_ID + ' .me-cats{display:flex;flex-wrap:wrap;gap:.35rem;padding:.65rem .75rem;border-bottom:1px solid #ddd9d2}',
    '#' + ROOT_ID + ' .me-cat{border:1px solid #ddd9d2;background:#fff;color:#1a1a1a;border-radius:6px;padding:.2rem .55rem;font-size:.7rem;font-weight:600;cursor:pointer}',
    '#' + ROOT_ID + ' .me-cat.is-active{background:#e8efec;border-color:#1f4b3a;color:#1f4b3a}',
    '#' + ROOT_ID + ' .me-search{padding:.55rem .75rem;border-bottom:1px solid #ddd9d2}',
    '#' + ROOT_ID + ' .me-search input{width:100%;border:1px solid #ddd9d2;border-radius:6px;padding:.4rem .55rem;font-size:.8125rem}',
    '#' + ROOT_ID + ' .me-search input:focus{outline:none;border-color:#1f4b3a;box-shadow:0 0 0 2px rgba(31,75,58,.12)}',
    '#' + ROOT_ID + ' .me-list{flex:1;overflow:auto;padding:.25rem 0}',
    '#' + ROOT_ID + ' .me-item{display:flex;justify-content:space-between;gap:.5rem;width:100%;text-align:left;border:0;border-bottom:1px solid #f0eee8;background:transparent;padding:.55rem .85rem;cursor:pointer}',
    '#' + ROOT_ID + ' .me-item:hover{background:#ebeae4}',
    '#' + ROOT_ID + ' .me-item.is-active{background:#e8efec}',
    '#' + ROOT_ID + ' .me-item .me-name{font-weight:500;word-break:break-word}',
    '#' + ROOT_ID + ' .me-item .me-count{flex-shrink:0;color:#6b6b66;font-variant-numeric:tabular-nums;font-size:.75rem}',
    '#' + ROOT_ID + ' .me-panel{padding:1rem 1.1rem;overflow:auto;flex:1}',
    '#' + ROOT_ID + ' .me-panel h2{margin:0 0 .35rem;font-size:1.05rem}',
    '#' + ROOT_ID + ' .me-panel .me-sub{color:#6b6b66;margin-bottom:1rem;font-size:.75rem}',
    '#' + ROOT_ID + ' .me-doc{display:block;padding:.7rem .85rem;margin:0 0 .5rem;background:#fff;border:1px solid #ddd9d2;border-radius:6px;text-decoration:none;color:inherit}',
    '#' + ROOT_ID + ' .me-doc:hover{border-color:#1f4b3a;background:#e8efec}',
    '#' + ROOT_ID + ' .me-doc .me-doc-title{font-weight:600;color:#1f4b3a;margin-bottom:.2rem}',
    '#' + ROOT_ID + ' .me-doc .me-doc-path{color:#6b6b66;font-size:.72rem;word-break:break-all}',
    '#' + ROOT_ID + ' .me-empty{padding:1.25rem;color:#6b6b66}',
    '#' + ROOT_ID + ' .me-error{padding:.35rem .75rem;color:#8a2f2f;font-size:.75rem}',
    /* Hide Datashare NLP task UI when our browser is active */
    'body.marshal-entities-active .task-entities-list,',
    'body.marshal-entities-active [class*="TaskEntitiesList"],',
    'body.marshal-entities-active a[href*="#/tasks/entities/new"],',
    'body.marshal-entities-active .page-header-toolbar [href*="entities/new"],',
    'body.marshal-entities-active .empty-state{display:none!important}',
    '#' + ROOT_ID + '{position:relative;z-index:5}',
  ].join('\n');

  function upsertStyle() {
    var el = document.getElementById(STYLE_ID);
    if (!el) {
      el = document.createElement('style');
      el.id = STYLE_ID;
      (document.head || document.documentElement).appendChild(el);
    }
    el.textContent = css;
  }

  function isEntitiesRoute() {
    var h = location.hash || '';
    if (h.indexOf('/tasks/entities/new') !== -1) return 'new';
    if (h.indexOf('/tasks/entities') !== -1) return 'list';
    return null;
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function projectCandidates(core) {
    var names = [];
    function add(name) {
      if (!name || typeof name !== 'string') return;
      name = name.trim();
      if (!name || SKIP_PROJECTS[name] || names.indexOf(name) !== -1) return;
      names.push(name);
    }
    try {
      var stored = localStorage.getItem('marshal-entities-project');
      add(stored);
    } catch (e) {}
    try {
      var q = new URLSearchParams(location.hash.split('?')[1] || '');
      var indices = q.get('indices') || q.get('project');
      if (indices) indices.split(',').forEach(add);
    } catch (e) {}
    try {
      if (core && typeof core.getDefaultProject === 'function') {
        var p = core.getDefaultProject();
        add(typeof p === 'string' ? p : p && p.name);
      }
    } catch (e) {}
    // Common MarshalOSS case slug on this host
    add('david-jones');
    add('washington');
    return names;
  }

  async function esSearch(core, project, body) {
    var api = core && core.api && core.api.elasticsearch;
    if (!api || typeof api.search !== 'function') {
      throw new Error('Datashare Elasticsearch API not available');
    }
    try {
      return await api.search({ index: project, body: body });
    } catch (err) {
      var msg = (err && err.message) || String(err);
      var status = err && (err.status || err.statusCode || (err.response && err.response.status));
      if (status) msg = 'Request failed with status code ' + status;
      // Surface missing-index clearly
      var data = err && err.response && err.response.data;
      if (data && data.error && data.error.type === 'index_not_found_exception') {
        msg = 'Project index not found: ' + project;
      }
      var e2 = new Error(msg);
      e2.cause = err;
      throw e2;
    }
  }

  async function projectHasEntities(core, project) {
    try {
      var res = await esSearch(core, project, {
        size: 0,
        query: { term: { type: 'NamedEntity' } },
        track_total_hits: true,
      });
      var total = (((res || {}).hits || {}).total || {}).value;
      if (typeof total !== 'number' && res && res.hits && res.hits.total) {
        total = typeof res.hits.total === 'number' ? res.hits.total : 0;
      }
      return total > 0;
    } catch (e) {
      return false;
    }
  }

  async function resolveProject(core) {
    var candidates = projectCandidates(core);
    // Prefer stored / URL candidates that actually have NamedEntities
    for (var i = 0; i < candidates.length; i++) {
      if (await projectHasEntities(core, candidates[i])) {
        return candidates[i];
      }
    }
    // Fall back to first candidate even if empty (so UI can show empty state)
    if (candidates.length) return candidates[0];
    return 'david-jones';
  }

  async function listProjects(core) {
    var found = [];
    var seen = {};
    var probe = projectCandidates(core);
    // Also try Datashare project API if present
    try {
      if (core && core.api && typeof core.api.getProject === 'function') {
        /* single-get only; ignore */
      }
    } catch (e) {}
    for (var i = 0; i < probe.length; i++) {
      var name = probe[i];
      if (seen[name]) continue;
      seen[name] = true;
      if (await projectHasEntities(core, name)) found.push(name);
    }
    // Always include current project even if empty
    if (state.project && found.indexOf(state.project) === -1) found.unshift(state.project);
    if (!found.length) found = probe.slice(0, 3);
    return found;
  }

  async function loadEntities() {
    var core = coreRef;
    var project = state.project;
    if (!core || !project) return;
    state.loadingList = true;
    state.error = null;
    render();
    try {
      var body = {
        size: 0,
        query: {
          bool: {
            must: [{ term: { type: 'NamedEntity' } }],
            filter: [
              { term: { isHidden: false } },
              { term: { category: state.category } },
            ],
          },
        },
        aggs: {
          byMentions: {
            terms: {
              field: 'mentionNorm',
              size: 500,
              order: { _count: 'desc' },
            },
            aggs: {
              sample: {
                top_hits: { size: 1, _source: ['mention', 'category'] },
              },
            },
          },
        },
      };
      var res = await esSearch(core, project, body);
      var buckets = (((res || {}).aggregations || {}).byMentions || {}).buckets || [];
      state.entitiesAll = buckets.map(function (b) {
        var hit = ((((b.sample || {}).hits || {}).hits) || [])[0];
        var mention = hit && hit._source && hit._source.mention ? hit._source.mention : b.key;
        return {
          mentionNorm: b.key,
          mention: mention,
          count: b.doc_count,
          category: state.category,
        };
      });
      applyFilter();
      if (state.selected) {
        var still = state.entitiesAll.some(function (e) {
          return e.mentionNorm === state.selected.mentionNorm;
        });
        if (!still) {
          state.selected = null;
          state.docs = [];
        }
      }
    } catch (err) {
      state.error = (err && err.message) || String(err);
      state.entitiesAll = [];
      state.entities = [];
    }
    state.loadingList = false;
    render();
  }

  function applyFilter() {
    var q = (state.q || '').trim().toLowerCase();
    state.entities = !q
      ? state.entitiesAll.slice()
      : state.entitiesAll.filter(function (e) {
          return (
            e.mention.toLowerCase().indexOf(q) !== -1 ||
            e.mentionNorm.indexOf(q) !== -1
          );
        });
  }

  async function loadDocs(entity) {
    var core = coreRef;
    var project = state.project;
    if (!core || !project || !entity) return;
    state.loadingDocs = true;
    state.error = null;
    render();
    try {
      // Collect parent document ids from NamedEntity children, then load those Documents
      var ne = await esSearch(core, project, {
        size: 0,
        query: {
          bool: {
            must: [
              { term: { type: 'NamedEntity' } },
              { term: { mentionNorm: entity.mentionNorm } },
              { term: { category: entity.category } },
              { term: { isHidden: false } },
            ],
          },
        },
        aggs: {
          docs: { terms: { field: 'documentId', size: 200 } },
        },
      });
      var idList = ((((ne || {}).aggregations || {}).docs || {}).buckets || []).map(function (b) {
        return b.key;
      });
      if (!idList.length) {
        state.docs = [];
      } else {
        var docs = await esSearch(core, project, {
          size: Math.min(idList.length, 200),
          query: {
            bool: {
              must: [{ term: { type: 'Document' } }, { ids: { values: idList } }],
            },
          },
          _source: ['title', 'path', 'contentType', 'tags'],
        });
        state.docs = (((docs || {}).hits || {}).hits || []).map(function (h) {
          var src = h._source || {};
          return {
            id: h._id,
            title: src.title || src.path || h._id,
            path: src.path || '',
            contentType: src.contentType || '',
          };
        });
        state.docs.sort(function (a, b) {
          return String(a.title).localeCompare(String(b.title));
        });
      }
    } catch (err) {
      state.error = (err && err.message) || String(err);
      state.docs = [];
    }
    state.loadingDocs = false;
    render();
  }

  function openDoc(doc) {
    var core = coreRef;
    var project = state.project;
    if (!core || !core.router) {
      location.hash = '#/d/' + encodeURIComponent(project) + '/' + encodeURIComponent(doc.id);
      return;
    }
    try {
      core.router.push({
        name: 'document',
        params: { index: project, id: doc.id, routing: doc.id },
      });
    } catch (e) {
      location.hash = '#/d/' + encodeURIComponent(project) + '/' + encodeURIComponent(doc.id);
    }
  }

  function selectEntity(entity) {
    state.selected = entity;
    loadDocs(entity);
    render();
  }

  function render() {
    var root = document.getElementById(ROOT_ID);
    if (!root) return;

    var cats = CATEGORIES.map(function (c) {
      return (
        '<button type="button" class="me-cat' +
        (c.id === state.category ? ' is-active' : '') +
        '" data-cat="' +
        esc(c.id) +
        '">' +
        esc(c.label) +
        '</button>'
      );
    }).join('');

    var items = state.entities
      .map(function (e) {
        var active = state.selected && state.selected.mentionNorm === e.mentionNorm;
        return (
          '<button type="button" class="me-item' +
          (active ? ' is-active' : '') +
          '" data-norm="' +
          esc(e.mentionNorm) +
          '">' +
          '<span class="me-name">' +
          esc(e.mention) +
          '</span>' +
          '<span class="me-count">' +
          e.count +
          '</span>' +
          '</button>'
        );
      })
      .join('');

    if (state.loadingList && !items) {
      items = '<div class="me-empty">Loading entities…</div>';
    } else if (!items) {
      items = '<div class="me-empty">No entities in this category.</div>';
    }

    var right = '';
    if (!state.selected) {
      right = '<div class="me-empty">Select an entity to see documents that mention it.</div>';
    } else if (state.loadingDocs) {
      right =
        '<h2>' +
        esc(state.selected.mention) +
        '</h2><div class="me-sub">Loading documents…</div>';
    } else {
      var docsHtml = state.docs
        .map(function (d) {
          return (
            '<a href="#" class="me-doc" data-doc="' +
            esc(d.id) +
            '">' +
            '<div class="me-doc-title">' +
            esc(d.title) +
            '</div>' +
            (d.path ? '<div class="me-doc-path">' + esc(d.path) + '</div>' : '') +
            '</a>'
          );
        })
        .join('');
      right =
        '<h2>' +
        esc(state.selected.mention) +
        '</h2>' +
        '<div class="me-sub">' +
        esc(state.selected.category) +
        ' · ' +
        state.docs.length +
        ' document' +
        (state.docs.length === 1 ? '' : 's') +
        ' · ' +
        state.selected.count +
        ' mention' +
        (state.selected.count === 1 ? '' : 's') +
        '</div>' +
        (docsHtml || '<div class="me-empty">No linked documents found.</div>');
    }

    var projectOpts = (state.projects.length ? state.projects : [state.project || 'david-jones'])
      .map(function (p) {
        return (
          '<option value="' +
          esc(p) +
          '"' +
          (p === state.project ? ' selected' : '') +
          '>' +
          esc(p) +
          '</option>'
        );
      })
      .join('');

    root.innerHTML =
      '<div class="me-header">' +
      '<div><h1>Entities</h1><div class="me-meta">' +
      '<label>Project <select class="me-project">' +
      projectOpts +
      '</select></label>' +
      '</div></div>' +
      (state.error ? '<div class="me-error">' + esc(state.error) + '</div>' : '') +
      '</div>' +
      '<div class="me-body">' +
      '<div class="me-left">' +
      '<div class="me-cats">' +
      cats +
      '</div>' +
      '<div class="me-search"><input type="search" placeholder="Filter entities…" value="' +
      esc(state.q) +
      '" /></div>' +
      '<div class="me-list">' +
      items +
      '</div>' +
      '</div>' +
      '<div class="me-right"><div class="me-panel">' +
      right +
      '</div></div>' +
      '</div>';

    bind(root);
  }

  function bind(root) {
    var projectSel = root.querySelector('.me-project');
    if (projectSel) {
      projectSel.addEventListener('change', function () {
        state.project = projectSel.value;
        try {
          localStorage.setItem('marshal-entities-project', state.project);
        } catch (e) {}
        state.selected = null;
        state.docs = [];
        state.entitiesAll = [];
        state.entities = [];
        loadEntities();
      });
    }
    root.querySelectorAll('.me-cat').forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.category = btn.getAttribute('data-cat');
        state.selected = null;
        state.docs = [];
        loadEntities();
      });
    });
    var input = root.querySelector('.me-search input');
    if (input) {
      input.addEventListener('input', function () {
        state.q = input.value || '';
        applyFilter();
        render();
        // restore focus/caret
        var again = document.querySelector('#' + ROOT_ID + ' .me-search input');
        if (again) {
          again.focus();
          var len = again.value.length;
          try {
            again.setSelectionRange(len, len);
          } catch (e) {}
        }
      });
    }
    root.querySelectorAll('.me-item').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var norm = btn.getAttribute('data-norm');
        var ent = state.entities.find(function (e) {
          return e.mentionNorm === norm;
        });
        if (ent) selectEntity(ent);
      });
    });
    root.querySelectorAll('.me-doc').forEach(function (a) {
      a.addEventListener('click', function (ev) {
        ev.preventDefault();
        var id = a.getAttribute('data-doc');
        var doc = state.docs.find(function (d) {
          return d.id === id;
        });
        if (doc) openDoc(doc);
      });
    });
  }

  function findMountParent() {
    return (
      document.querySelector('.page-container') ||
      document.querySelector('main') ||
      document.querySelector('#app') ||
      document.body
    );
  }

  async function mount() {
    upsertStyle();
    document.body.classList.add('marshal-entities-active');
    var parent = findMountParent();
    var root = document.getElementById(ROOT_ID);
    if (!root) {
      root = document.createElement('div');
      root.id = ROOT_ID;
      parent.appendChild(root);
    }
    mounted = true;
    state.error = null;
    state.loadingList = true;
    render();
    try {
      state.project = await resolveProject(coreRef);
      state.projects = await listProjects(coreRef);
      try {
        localStorage.setItem('marshal-entities-project', state.project);
      } catch (e) {}
    } catch (err) {
      state.project = state.project || 'david-jones';
      state.error = (err && err.message) || String(err);
    }
    render();
    await loadEntities();
  }

  function unmount() {
    document.body.classList.remove('marshal-entities-active');
    var root = document.getElementById(ROOT_ID);
    if (root && root.parentNode) root.parentNode.removeChild(root);
    mounted = false;
  }

  function syncRoute() {
    var kind = isEntitiesRoute();
    if (kind === 'new' && coreRef && coreRef.router) {
      try {
        coreRef.router.replace({ name: 'task.entities.list' });
      } catch (e) {
        location.hash = '#/tasks/entities';
      }
      return;
    }
    if (kind === 'list') {
      if (!mounted) mount();
      else if (!document.getElementById(ROOT_ID)) mount();
    } else if (mounted) {
      unmount();
    }
  }

  function replaceRoutes(core) {
    var Browser = {
      name: 'MarshalEntitiesBrowser',
      render: function () {
        return null;
      },
      mounted: function () {
        mount();
      },
      unmounted: function () {
        unmount();
      },
      beforeUnmount: function () {
        unmount();
      },
    };

    try {
      if (typeof core.router.removeRoute === 'function') {
        try {
          core.router.removeRoute('task.entities.list');
        } catch (e) {}
        try {
          core.router.removeRoute('task.entities.new');
        } catch (e) {}
      }
      core.router.addRoute('task.entities', {
        name: 'task.entities.list',
        path: '',
        component: Browser,
        meta: { title: 'Entities', breadcrumb: false },
      });
      // Keep /new but bounce to list
      core.router.addRoute('task.entities', {
        name: 'task.entities.new',
        path: 'new',
        redirect: { name: 'task.entities.list' },
        meta: { title: 'Entities' },
      });
    } catch (err) {
      console.warn('[marshal-entities] route replace failed, using hash fallback', err);
    }

    try {
      core.i18n.global.mergeLocaleMessage('en', {
        task: {
          entities: {
            title: 'Entities',
            list: {
              emptyStateLabel: 'Loading entities…',
              emptyStateAction: 'Entities',
              info: 'Browse people, organizations, places, and other entities across the case.',
            },
          },
        },
      });
    } catch (e) {}
  }

  function boot(core) {
    coreRef = core || coreRef;
    upsertStyle();
    if (coreRef) replaceRoutes(coreRef);
    syncRoute();
  }

  document.addEventListener('datashare:ready', function (ev) {
    var core = ev && ev.detail && ev.detail.core;
    boot(core);
  });

  window.addEventListener('hashchange', syncRoute);
  window.addEventListener('popstate', syncRoute);

  // In case datashare:ready already fired
  setTimeout(function () {
    if (!coreRef) boot(null);
    syncRoute();
  }, 1500);
  setTimeout(syncRoute, 3000);
})();
