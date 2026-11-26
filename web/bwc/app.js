(() => {
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];

  const state = {
    index: null,
    activeId: null,
    expandedOfficer: null,
    query: "",
    tab: "transcript",
    timelineExpanded: false,
    timelineDay: null, // "YYYY-MM-DD" or null (= auto / first day)
    timelineSelectedOfficers: null, // null = all; string[] when filtered
    timelineFilterOpen: false,
    timelineAdjustMode: false,
    timelineOffsets: {}, // videoId -> offset seconds
    timelineOffsetEditId: null,
  };
  const TIMELINE_COLLAPSED_ROWS = 5;
  const DAY_MS = 24 * 60 * 60 * 1000;
  const QUARTER_HOUR_MS = 15 * 60 * 1000;
  const HALF_HOUR_MS = 30 * 60 * 1000;

  let player = null;
  let audio = null;
  let bootGeneration = 0;
  let wired = false;
  let lastFollowedSeg = -1;
  let pinnedSegIndex = null;
  let pinUntilSec = null;
  let labelSuggestionsLoaded = false;
  let labelSuggestions = []; // { label, type }
  let labelBusy = false;
  let labelMenuActive = -1;
  let labelMenuQuery = "";
  let labelMenuIgnoreDocClick = false;
  const TIMELINE_COLORS = [
    "#2563eb", "#dc2626", "#059669", "#9333ea", "#ea580c",
    "#0891b2", "#c026d3", "#4f46e5", "#65a30d", "#b45309",
    "#0f766e", "#be123c", "#7c3aed", "#0369a1", "#15803d",
    "#a21caf", "#c2410c", "#1d4ed8", "#047857", "#9f1239",
  ];

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function fmtRange(start, end) {
    if (end == null || end <= start) return fmt(start);
    return `${fmt(start)}–${fmt(end)}`;
  }

  function parseRecordedStart(value) {
    const match = String(value || "").match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/,
    );
    if (!match) return null;
    const [, year, month, day, hour, minute, second] = match;
    return Date.UTC(
      Number(year), Number(month) - 1, Number(day),
      Number(hour), Number(minute), Number(second || 0),
    );
  }

  function fmtCameraTime(ms, withSeconds = false) {
    const date = new Date(ms);
    let hour = date.getUTCHours();
    const minute = String(date.getUTCMinutes()).padStart(2, "0");
    const second = String(date.getUTCSeconds()).padStart(2, "0");
    const suffix = hour >= 12 ? "PM" : "AM";
    hour = hour % 12 || 12;
    return `${hour}:${minute}${withSeconds ? `:${second}` : ""} ${suffix}`;
  }

  function fmtCameraDate(ms) {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(ms));
  }

  function cameraDayKey(ms) {
    const date = new Date(ms);
    const y = date.getUTCFullYear();
    const m = String(date.getUTCMonth() + 1).padStart(2, "0");
    const d = String(date.getUTCDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function dayStartMs(dayKey) {
    const [y, m, d] = String(dayKey).split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  }

  function fmtDayPill(dayKey) {
    return new Intl.DateTimeFormat("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    }).format(new Date(dayStartMs(dayKey)));
  }


  function offsetStorageKey() {
    const caseName = (state.index && state.index.case) || "bwc";
    return `marshal:bwc:timeline-offsets:${caseName}`;
  }

  function loadTimelineOffsets() {
    try {
      const raw = localStorage.getItem(offsetStorageKey());
      if (!raw) {
        state.timelineOffsets = {};
        return;
      }
      const parsed = JSON.parse(raw);
      state.timelineOffsets = parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      state.timelineOffsets = {};
    }
  }

  function saveTimelineOffsets() {
    try {
      localStorage.setItem(offsetStorageKey(), JSON.stringify(state.timelineOffsets || {}));
    } catch {
      /* ignore quota / private mode */
    }
  }

  function clipOffsetSec(videoId) {
    const value = Number(state.timelineOffsets?.[videoId] || 0);
    return Number.isFinite(value) ? value : 0;
  }

  function activeOffsetCount() {
    return Object.values(state.timelineOffsets || {}).filter((v) => Number(v) !== 0).length;
  }

  function fmtOffsetSec(sec) {
    const n = Math.round(Number(sec) || 0);
    const sign = n < 0 ? "-" : "+";
    const abs = Math.abs(n);
    const h = Math.floor(abs / 3600);
    const m = Math.floor((abs % 3600) / 60);
    const s = abs % 60;
    if (h > 0) return `${sign}${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${sign}${m}:${String(s).padStart(2, "0")}`;
  }

  function parseOffsetInput(raw) {
    const text = String(raw || "").trim();
    if (!text) return 0;
    if (/^[+-]?\d+$/.test(text)) return Number(text);
    const hms = text.match(/^([+-])?(\d+):([0-5]?\d):([0-5]?\d)$/);
    if (hms) {
      const sign = hms[1] === "-" ? -1 : 1;
      return sign * (Number(hms[2]) * 3600 + Number(hms[3]) * 60 + Number(hms[4]));
    }
    const ms = text.match(/^([+-])?(\d+):([0-5]?\d)$/);
    if (!ms) return null;
    const sign = ms[1] === "-" ? -1 : 1;
    return sign * (Number(ms[2]) * 60 + Number(ms[3]));
  }

  function setClipOffset(videoId, sec, { render = true } = {}) {
    if (!videoId) return;
    const limit = 7 * 24 * 3600; // keep absurd OCR errors usable, but bounded
    const next = Math.max(-limit, Math.min(limit, Math.round(Number(sec) || 0)));
    if (!state.timelineOffsets) state.timelineOffsets = {};
    if (next === 0) delete state.timelineOffsets[videoId];
    else state.timelineOffsets[videoId] = next;
    saveTimelineOffsets();
    updateOffsetChrome();
    if (render) renderTimeline();
  }

  function updateOffsetChrome() {
    const panel = $("#timelinePanel") || $(".timeline-panel");
    const adjustBtn = $("#timelineAdjustBtn");
    const count = $("#timelineOffsetCount");
    const offsetPanel = $("#timelineOffsetPanel");
    const title = $("#timelineOffsetTitle");
    const value = $("#timelineOffsetValue");
    const input = $("#timelineOffsetInput");
    const reset = $("#timelineOffsetReset");
    const hint = $("#timelineOffsetHint");
    const n = activeOffsetCount();

    if (panel) panel.classList.toggle("is-adjusting", state.timelineAdjustMode);
    if (adjustBtn) {
      adjustBtn.classList.toggle("is-mode", state.timelineAdjustMode);
      adjustBtn.setAttribute("aria-pressed", state.timelineAdjustMode ? "true" : "false");
    }
    if (count) {
      if (n > 0) {
        count.hidden = false;
        count.textContent = String(n);
      } else {
        count.hidden = true;
        count.textContent = "";
      }
    }
    if (!offsetPanel) return;
    offsetPanel.hidden = !state.timelineAdjustMode;
    if (!state.timelineAdjustMode) return;

    const editId = state.timelineOffsetEditId;
    const video = editId ? state.index?.videos?.find((v) => v.id === editId) : null;
    const offset = editId ? clipOffsetSec(editId) : 0;
    const hasEdit = Boolean(video);
    if (title) {
      title.textContent = hasEdit
        ? `${video.officer} — ${video.clipLabel}`
        : "Drag a clip to offset";
    }
    if (value) value.textContent = fmtOffsetSec(offset);
    if (input) {
      input.disabled = !hasEdit;
      if (document.activeElement !== input) input.value = hasEdit ? fmtOffsetSec(offset) : "";
    }
    if (reset) reset.disabled = !hasEdit || offset === 0;
    $$(".timeline-offset-nudge").forEach((btn) => {
      btn.disabled = !hasEdit;
    });
    if (hint) {
      hint.textContent = hasEdit
        ? "Drag the bar left/right, type ±h:mm:ss, or use the nudge buttons. Positive shifts later."
        : "Adjust times is on — drag a colored bar left/right to shift that recording.";
    }
  }

  /** Pure timeline model helpers — reusable across single- and multi-day cases. */
  function buildTimelineClips(videos) {
    return (videos || [])
      .map((video) => {
        const rawStart = parseRecordedStart(video.recorded_start);
        if (rawStart == null) return null;
        const offsetSec = clipOffsetSec(video.id);
        const start = rawStart + offsetSec * 1000;
        const durationMs = Math.max(0, (video.duration_sec || 0) * 1000);
        return {
          video,
          rawStart,
          offsetSec,
          start,
          end: start + durationMs,
          durationMs,
          dayKey: cameraDayKey(start),
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.start - b.start || a.video.id.localeCompare(b.video.id));
  }

  function collectTimelineDays(clips) {
    const days = new Set();
    for (const clip of clips) {
      const first = dayStartMs(clip.dayKey);
      const last = dayStartMs(cameraDayKey(Math.max(clip.start, clip.end - 1)));
      for (let t = first; t <= last; t += DAY_MS) days.add(cameraDayKey(t));
    }
    return [...days].sort();
  }

  function clipsForTimelineDay(clips, dayKey) {
    if (!dayKey) return clips;
    const start = dayStartMs(dayKey);
    const end = start + DAY_MS;
    return clips.filter((clip) => clip.start < end && clip.end > start);
  }

  function resolveTimelineDay(days, preferred) {
    if (!days.length) return null;
    if (preferred && days.includes(preferred)) return preferred;
    return days[0];
  }

  function timelineRangeForClips(clips, dayKey) {
    if (!clips.length) return null;
    let earliest = Math.min(...clips.map((c) => c.start));
    let latest = Math.max(...clips.map((c) => c.end));
    if (dayKey) {
      const dayStart = dayStartMs(dayKey);
      const dayEnd = dayStart + DAY_MS;
      earliest = Math.max(earliest, dayStart);
      latest = Math.min(latest, dayEnd);
    }
    const rangeStart = Math.floor(earliest / QUARTER_HOUR_MS) * QUARTER_HOUR_MS;
    const rangeEnd = Math.ceil(Math.max(earliest + 1, latest) / QUARTER_HOUR_MS) * QUARTER_HOUR_MS;
    return {
      rangeStart,
      rangeEnd,
      range: Math.max(1, rangeEnd - rangeStart),
    };
  }

  function buildTimelineLanes(clips) {
    const grouped = new Map();
    for (const clip of clips) {
      const officer = clip.video.officer;
      if (!grouped.has(officer)) grouped.set(officer, []);
      grouped.get(officer).push(clip);
    }
    return [...grouped.entries()]
      .map(([officer, items]) => ({
        officer,
        items: items.slice().sort((a, b) => a.start - b.start),
        start: Math.min(...items.map((item) => item.start)),
      }))
      .sort((a, b) => a.start - b.start || a.officer.localeCompare(b.officer));
  }

  function buildTimelineTicks(rangeStart, rangeEnd, range) {
    const ticks = [];
    for (
      let tick = Math.ceil(rangeStart / HALF_HOUR_MS) * HALF_HOUR_MS;
      tick <= rangeEnd;
      tick += HALF_HOUR_MS
    ) {
      ticks.push({ at: tick, left: ((tick - rangeStart) / range) * 100 });
    }
    return ticks;
  }


  function officerFilterActive(allOfficers) {
    if (state.timelineSelectedOfficers == null) return false;
    if (!allOfficers.length) return false;
    if (state.timelineSelectedOfficers.length !== allOfficers.length) return true;
    const selected = new Set(state.timelineSelectedOfficers);
    return allOfficers.some((name) => !selected.has(name));
  }

  function isTimelineOfficerSelected(name) {
    if (state.timelineSelectedOfficers == null) return true;
    return state.timelineSelectedOfficers.includes(name);
  }

  function setTimelineOfficerSelected(name, checked, allOfficers) {
    const current = state.timelineSelectedOfficers == null
      ? allOfficers.slice()
      : state.timelineSelectedOfficers.slice();
    const next = new Set(current);
    if (checked) next.add(name);
    else next.delete(name);
    if (next.size === allOfficers.length && allOfficers.every((n) => next.has(n))) {
      state.timelineSelectedOfficers = null;
    } else {
      state.timelineSelectedOfficers = allOfficers.filter((n) => next.has(n));
    }
  }

  function closeTimelineFilter() {
    state.timelineFilterOpen = false;
    const btn = $("#timelineFilterBtn");
    const menu = $("#timelineFilterMenu");
    if (btn) btn.setAttribute("aria-expanded", "false");
    if (menu) menu.hidden = true;
  }

  function openTimelineFilter() {
    state.timelineFilterOpen = true;
    const btn = $("#timelineFilterBtn");
    const menu = $("#timelineFilterMenu");
    if (btn) btn.setAttribute("aria-expanded", "true");
    if (menu) menu.hidden = false;
  }

  function renderTimelineFilter(allOfficers) {
    const btn = $("#timelineFilterBtn");
    const menu = $("#timelineFilterMenu");
    const list = $("#timelineFilterList");
    const count = $("#timelineFilterCount");
    if (!btn || !menu || !list || !count) return;

    const active = officerFilterActive(allOfficers);
    const selectedCount = state.timelineSelectedOfficers == null
      ? allOfficers.length
      : state.timelineSelectedOfficers.length;
    btn.classList.toggle("is-active", active);
    if (active) {
      count.hidden = false;
      count.textContent = String(selectedCount);
    } else {
      count.hidden = true;
      count.textContent = "";
    }

    list.innerHTML = allOfficers.length
      ? allOfficers.map((name) => {
          const checked = isTimelineOfficerSelected(name) ? "checked" : "";
          return `<label class="timeline-filter-option">
            <input type="checkbox" data-officer="${escapeHtml(name)}" ${checked} />
            <span>${escapeHtml(name)}</span>
          </label>`;
        }).join("")
      : `<div class="timeline-filter-empty">No officers available.</div>`;

    $$("#timelineFilterList input[type=checkbox]").forEach((input) => {
      input.addEventListener("change", () => {
        setTimelineOfficerSelected(input.dataset.officer, input.checked, allOfficers);
        state.timelineExpanded = false;
        renderTimeline();
        if (state.timelineFilterOpen) openTimelineFilter();
      });
    });

    if (state.timelineFilterOpen) openTimelineFilter();
    else closeTimelineFilter();
  }

  function renderTimelineDayBar(days, activeDay) {
    const bar = $("#timelineDayBar");
    const daysEl = $("#timelineDays");
    if (!bar || !daysEl) return;

    if (days.length <= 1) {
      bar.hidden = true;
      daysEl.innerHTML = "";
      return;
    }

    bar.hidden = false;
    daysEl.innerHTML = days
      .map((dayKey) => {
        const active = dayKey === activeDay ? "is-active" : "";
        return `<button type="button" class="timeline-day ${active}" role="tab" aria-selected="${dayKey === activeDay}" data-day="${dayKey}">${escapeHtml(fmtDayPill(dayKey))}</button>`;
      })
      .join("");

    $$("#timelineDays .timeline-day").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (state.timelineDay === btn.dataset.day) return;
        state.timelineDay = btn.dataset.day;
        state.timelineExpanded = false;
    state.timelineDay = null;
    state.timelineSelectedOfficers = null;
    state.timelineFilterOpen = false;
    state.timelineAdjustMode = false;
    state.timelineOffsetEditId = null;
    state.timelineOffsets = {};
        renderTimeline();
      });
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function highlight(text, query) {
    const safe = escapeHtml(text);
    if (!query.trim()) return safe;
    const terms = query
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!terms.length) return safe;
    const re = new RegExp(`(${terms.join("|")})`, "ig");
    return safe.replace(re, "<mark>$1</mark>");
  }

  function searching() {
    return Boolean(state.query.trim());
  }

  /** Officer / source name: prefer API label, else filename heuristics. */
  function officerFromVideo(v) {
    if (v.officer) return String(v.officer);
    if (v.kind === "audio") return "911 Call";
    if (v.device_serial && v.device) return `${v.device} · ${v.device_serial}`;
    if (v.device_serial) return `Bodycam · ${v.device_serial}`;
    if (v.device) return String(v.device);
    const base = String(v.filename || v.title || "")
      .replace(/\.[^.]+$/, "")
      .replace(/\s*-\s*confidential\s*$/i, "")
      .trim();
    const m = base.match(/^(.+?)\s*\((\d+)\)\s*$/);
    let name = (m ? m[1] : base).trim();
    name = name.replace(/^Po\b/, "PO");
    return name || "Unknown";
  }

  function clipLabel(v) {
    if (v.clipLabel) return String(v.clipLabel);
    const base = String(v.filename || v.title || "")
      .replace(/\.[^.]+$/, "")
      .replace(/\s*-\s*confidential\s*$/i, "")
      .trim();
    const m = base.match(/\((\d+)\)\s*$/);
    if (m) return `Clip ${m[1]}`;
    if (v.kind === "audio") return "Full recording";
    if (v.recorded_start && v.recorded_start.length >= 16) {
      return v.recorded_start.slice(11, 16);
    }
    return "Recording";
  }

  function enrichVideos() {
    state.index.videos = (state.index.videos || []).filter((v) => {
      if (v.kind === "audio") return false;
      const hay = `${v.filename || ""} ${v.title || ""}`.toLowerCase();
      return !/\b911\b/.test(hay);
    });
    for (const v of state.index.videos) {
      v.officer = officerFromVideo(v);
      v.clipLabel = clipLabel(v);
    }
  }

  function officers() {
    const map = new Map();
    for (const v of state.index.videos) {
      if (!map.has(v.officer)) {
        map.set(v.officer, {
          name: v.officer,
          kind: v.kind,
          videos: [],
        });
      }
      map.get(v.officer).videos.push(v);
    }
    for (const o of map.values()) {
      o.videos.sort((a, b) => (b.duration_sec || 0) - (a.duration_sec || 0) || a.title.localeCompare(b.title));
      o.duration_sec = o.videos.reduce((n, v) => n + (v.duration_sec || 0), 0);
      o.hitCount = o.videos.reduce((n, v) => n + videoHitCount(v), 0);
    }
    return [...map.values()].sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "audio" ? 1 : -1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });
  }

  function activeMedia() {
    return state.index.videos.find((x) => x.id === state.activeId);
  }

  function mediaEl(v) {
    return v && v.kind === "audio" ? audio : player;
  }

  function scoreSegment(seg, query) {
    if (!query.trim()) return 0;
    const hay = seg.text.toLowerCase();
    const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    let score = 0;
    for (const t of terms) {
      if (!hay.includes(t)) return 0;
      score += 1;
      if (hay.includes(` ${t} `) || hay.startsWith(t)) score += 0.5;
    }
    return score;
  }

  function collectHits({ officer = null, videoId = null } = {}) {
    const q = state.query;
    const hits = [];
    for (const v of state.index.videos) {
      if (officer && v.officer !== officer) continue;
      if (videoId && v.id !== videoId) continue;
      v.segments.forEach((seg, i) => {
        const score = scoreSegment(seg, q);
        if (score > 0) hits.push({ video: v, seg, i, score });
      });
    }
    hits.sort((a, b) => b.score - a.score || a.seg.start - b.seg.start);
    return hits;
  }

  function collectHitsForOnScreen() {
    const active = activeMedia();
    if (!active) return [];
    // Hits panel is scoped to the officer whose recording is on screen
    return collectHits({ officer: active.officer });
  }

  function videoHitCount(v) {
    if (!searching()) return 0;
    return v.segments.reduce((n, seg) => n + (scoreSegment(seg, state.query) > 0 ? 1 : 0), 0);
  }

  function syncShell() {
    const shell = document.querySelector(".bwc-shell");
    if (shell) shell.classList.remove("picking");
  }

  function updateSearchChrome() {
    const active = searching();
    $("#searchWrap").classList.toggle("is-searching", active);
    $("#clearSearch").hidden = !active;
    $$(".tab-hits").forEach((t) => {
      t.classList.toggle("is-lit", active);
      t.disabled = !active;
      t.setAttribute("aria-disabled", active ? "false" : "true");
    });
    if (!active && state.tab === "hits") setTab("transcript");

    if (!active) {
      $("#hitMeta").textContent = "";
      return;
    }

    const hits = collectHits();
    const officersHit = new Set(hits.map((h) => h.video.officer)).size;
    $("#hitMeta").innerHTML = `Filtering by “<strong>${escapeHtml(state.query.trim())}</strong>” · ${hits.length} hit${hits.length === 1 ? "" : "s"} across ${officersHit} source${officersHit === 1 ? "" : "s"}`;
  }

  function clearSearch() {
    state.query = "";
    $("#q").value = "";
    updateSearchChrome();
    renderList();
    renderTranscript();
    renderVision();
    renderHits();
  }

  function closeAllSelects() {
    $$(".cselect").forEach((el) => {
      el.classList.remove("open");
      const trigger = el.querySelector(".cselect-trigger");
      const menu = el.querySelector(".cselect-menu");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
      if (menu) menu.hidden = true;
    });
  }

  function openSelect(kind) {
    const root = kind === "officer" ? $("#officerSelect") : $("#clipSelect");
    if (!root) return;
    const already = root.classList.contains("open");
    closeAllSelects();
    if (already) return;
    const trigger = root.querySelector(".cselect-trigger");
    const menu = root.querySelector(".cselect-menu");
    if (!trigger || trigger.disabled || !menu) return;
    root.classList.add("open");
    trigger.setAttribute("aria-expanded", "true");
    menu.hidden = false;
  }

  function renderTimeline() {
    const box = $("#timeline");
    const note = $("#timelineNote");
    if (!box || !note || !state.index) return;

    const allClips = buildTimelineClips(state.index.videos);
    const days = collectTimelineDays(allClips);
    const activeDay = resolveTimelineDay(days, state.timelineDay);
    if (activeDay && state.timelineDay !== activeDay) state.timelineDay = activeDay;

    renderTimelineDayBar(days, activeDay);

    const hideToggle = () => {
      const toggle = $("#timelineToggle");
      if (toggle) toggle.hidden = true;
    };

    if (!allClips.length) {
      box.innerHTML = `<div class="timeline-empty">No camera timestamps are available.</div>`;
      note.textContent = "";
      hideToggle();
      renderTimelineFilter([]);
      wireTimelineFilterControls([]);
      wireTimelineOffsetControls();
      updateOffsetChrome();
      return;
    }

    const multiDay = days.length > 1;
    const timed = clipsForTimelineDay(allClips, multiDay ? activeDay : null);
    if (!timed.length) {
      box.innerHTML = `<div class="timeline-empty">No recordings on this day.</div>`;
      note.textContent = activeDay ? fmtCameraDate(dayStartMs(activeDay)) : "";
      hideToggle();
      renderTimelineFilter([]);
      wireTimelineFilterControls([]);
      wireTimelineOffsetControls();
      updateOffsetChrome();
      return;
    }

    const rangeInfo = timelineRangeForClips(timed, multiDay ? activeDay : null);
    const { rangeStart, rangeEnd, range } = rangeInfo;
    const allLanes = buildTimelineLanes(timed);
    const allOfficers = allLanes.map((lane) => lane.officer);
    renderTimelineFilter(allOfficers);
    const lanes = allLanes.filter((lane) => isTimelineOfficerSelected(lane.officer));
    const hiddenCount = Math.max(0, lanes.length - TIMELINE_COLLAPSED_ROWS);
    const visibleLanes = state.timelineExpanded
      ? lanes
      : lanes.slice(0, TIMELINE_COLLAPSED_ROWS);
    if (!lanes.length) {
      box.innerHTML = `<div class="timeline-empty">No officers selected. Use the filter to choose officers.</div>`;
      note.textContent = multiDay ? "" : fmtCameraDate(rangeStart);
      hideToggle();
      wireTimelineFilterControls(allOfficers);
      wireTimelineOffsetControls();
      updateOffsetChrome();
      return;
    }
    const ticks = buildTimelineTicks(rangeStart, rangeEnd, range);
    const tickLines = ticks
      .map((tick) => `<span class="timeline-gridline" style="left:${tick.left}%"></span>`)
      .join("");
    const axisTicks = ticks
      .map((tick) => `<span class="timeline-tick" style="left:${tick.left}%">${fmtCameraTime(tick.at)}</span>`)
      .join("");

    const rows = visibleLanes
      .map((lane) => {
        const laneIndex = Math.max(0, allLanes.findIndex((item) => item.officer === lane.officer));
        const color = TIMELINE_COLORS[laneIndex % TIMELINE_COLORS.length];
        const clips = lane.items
          .map((clip) => {
            const { video, start, end } = clip;
            const left = ((start - rangeStart) / range) * 100;
            const width = Math.max(0.12, (clip.durationMs / range) * 100);
            const active = video.id === state.activeId ? "is-active" : "";
            const muted = searching() && videoHitCount(video) === 0 ? "is-muted" : "";
            const progress = active
              ? Math.min(100, Math.max(0, ((mediaEl(video)?.currentTime || 0) / Math.max(1, video.duration_sec)) * 100))
              : 0;
            const offsetSec = clip.offsetSec || 0;
            const hasOffset = offsetSec !== 0;
            const editing = state.timelineOffsetEditId === video.id ? "is-editing" : "";
            const offsetClass = hasOffset ? "has-offset" : "";
            const rawStart = clip.rawStart ?? (start - offsetSec * 1000);
            const title = `${lane.officer} — ${video.clipLabel}\n${fmtCameraTime(start, true)}–${fmtCameraTime(end, true)}${hasOffset ? `\nOffset ${fmtOffsetSec(offsetSec)} (was ${fmtCameraTime(rawStart, true)})` : ""}`;
            const chip = hasOffset ? `<span class="timeline-clip-offset">${fmtOffsetSec(offsetSec)}</span>` : "";
            return `<button type="button" class="timeline-clip ${active} ${muted} ${offsetClass} ${editing}" data-id="${video.id}" style="left:${left}%;width:max(${width}%, 4px);--lane-color:${color}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">
              <span class="timeline-progress" style="width:${progress}%"></span>
              ${chip}
            </button>`;
          })
          .join("");
        return `<div class="timeline-row">
          <div class="timeline-officer" title="${escapeHtml(lane.officer)}">
            <span class="timeline-swatch" style="background:${color}"></span>
            <span>${escapeHtml(lane.officer)}</span>
          </div>
          <div class="timeline-track">${tickLines}${clips}</div>
        </div>`;
      })
      .join("");

    box.innerHTML = `<div class="timeline-canvas" data-range-start="${rangeStart}" data-range="${range}">
      <div class="timeline-axis">
        <div class="timeline-axis-label">Camera time</div>
        <div class="timeline-axis-track">${axisTicks}</div>
      </div>
      <div class="timeline-body">
        ${rows}
        <div class="timeline-cursor" id="timelineCursor" hidden>
          <div class="timeline-cursor-line"></div>
          <div class="timeline-cursor-label" id="timelineCursorLabel"></div>
        </div>
      </div>
    </div>`;
    note.textContent = multiDay ? `${days.length} days` : fmtCameraDate(rangeStart);

    const toggle = $("#timelineToggle");
    const toggleLabel = $("#timelineToggleLabel");
    if (toggle && toggleLabel) {
      if (!hiddenCount) {
        toggle.hidden = true;
      } else {
        toggle.hidden = false;
        toggle.classList.toggle("is-expanded", state.timelineExpanded);
        toggle.setAttribute("aria-expanded", state.timelineExpanded ? "true" : "false");
        toggleLabel.textContent = state.timelineExpanded
          ? "Show less"
          : `Show ${hiddenCount} more officer${hiddenCount === 1 ? "" : "s"}`;
        toggle.title = state.timelineExpanded
          ? "Collapse officer lanes"
          : `Show ${hiddenCount} more officer${hiddenCount === 1 ? "" : "s"}`;
      }
    }

    updateOffsetChrome();
    wireTimelineInteractions(rangeStart, range);
    wireTimelineFilterControls(allOfficers);
    wireTimelineOffsetControls();
  }


  function wireTimelineFilterControls(allOfficers) {
    const btn = $("#timelineFilterBtn");
    const menu = $("#timelineFilterMenu");
    const allBtn = $("#timelineFilterAll");
    const clearBtn = $("#timelineFilterClear");
    if (!btn || !menu) return;

    if (!btn.dataset.wired) {
      btn.dataset.wired = "1";
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (state.timelineFilterOpen) closeTimelineFilter();
        else openTimelineFilter();
      });
      menu.addEventListener("click", (e) => e.stopPropagation());
    }
    if (allBtn && !allBtn.dataset.wired) {
      allBtn.dataset.wired = "1";
      allBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        state.timelineSelectedOfficers = null;
        state.timelineExpanded = false;
        renderTimeline();
        openTimelineFilter();
      });
    }
    if (clearBtn && !clearBtn.dataset.wired) {
      clearBtn.dataset.wired = "1";
      clearBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        state.timelineSelectedOfficers = [];
        state.timelineExpanded = false;
        renderTimeline();
        openTimelineFilter();
      });
    }

    // Keep references fresh for current officer universe.
    btn.dataset.officerCount = String(allOfficers.length);
  }


  function wireTimelineOffsetControls() {
    const adjustBtn = $("#timelineAdjustBtn");
    const input = $("#timelineOffsetInput");
    const reset = $("#timelineOffsetReset");
    if (adjustBtn && !adjustBtn.dataset.wired) {
      adjustBtn.dataset.wired = "1";
      adjustBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        closeTimelineFilter();
        state.timelineAdjustMode = !state.timelineAdjustMode;
        if (!state.timelineAdjustMode) state.timelineOffsetEditId = null;
        updateOffsetChrome();
        renderTimeline();
      });
    }
    if (input && !input.dataset.wired) {
      input.dataset.wired = "1";
      const commit = () => {
        if (!state.timelineOffsetEditId) return;
        const parsed = parseOffsetInput(input.value);
        if (parsed == null) {
          input.value = fmtOffsetSec(clipOffsetSec(state.timelineOffsetEditId));
          return;
        }
        setClipOffset(state.timelineOffsetEditId, parsed, { render: true });
      };
      input.addEventListener("change", commit);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
          input.blur();
        }
      });
    }
    if (reset && !reset.dataset.wired) {
      reset.dataset.wired = "1";
      reset.addEventListener("click", () => {
        if (!state.timelineOffsetEditId) return;
        setClipOffset(state.timelineOffsetEditId, 0, { render: true });
      });
    }
    $$(".timeline-offset-nudge").forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", () => {
        if (!state.timelineOffsetEditId) return;
        const nudge = Number(btn.dataset.nudge || 0);
        setClipOffset(
          state.timelineOffsetEditId,
          clipOffsetSec(state.timelineOffsetEditId) + nudge,
          { render: true },
        );
      });
    });
    const panel = $("#timelineOffsetPanel");
    if (panel && !panel.dataset.wired) {
      panel.dataset.wired = "1";
      panel.addEventListener("click", (e) => e.stopPropagation());
    }
  }

  function wireTimelineInteractions(rangeStart, range) {
    const canvas = $("#timeline .timeline-canvas");
    const body = $("#timeline .timeline-body");
    const cursor = $("#timelineCursor");
    const cursorLabel = $("#timelineCursorLabel");
    const toggle = $("#timelineToggle");

    if (toggle && !toggle.dataset.wired) {
      toggle.dataset.wired = "1";
      toggle.addEventListener("click", () => {
        state.timelineExpanded = !state.timelineExpanded;
        renderTimeline();
      });
    }

    $$("#timeline .timeline-clip").forEach((clip) => {
      clip.addEventListener("pointerdown", (event) => {
        if (!state.timelineAdjustMode) return;
        if (event.button != null && event.button !== 0) return;
        const video = state.index.videos.find((item) => item.id === clip.dataset.id);
        const canvas = $("#timeline .timeline-canvas");
        const track = clip.closest(".timeline-track");
        if (!video || !canvas || !track) return;
        event.preventDefault();
        event.stopPropagation();

        const rangeStart = Number(canvas.dataset.rangeStart);
        const range = Number(canvas.dataset.range) || 1;
        const trackRect = () => track.getBoundingClientRect();
        const pxToSec = (dx) => (dx / Math.max(1, trackRect().width)) * (range / 1000);
        const originOffset = clipOffsetSec(video.id);
        const originX = event.clientX;
        let moved = false;
        let latest = originOffset;

        state.timelineOffsetEditId = video.id;
        updateOffsetChrome();
        clip.classList.add("is-editing", "is-dragging");
        clip.setPointerCapture?.(event.pointerId);

        const paint = (offsetSec) => {
          latest = offsetSec;
          const rawStart = parseRecordedStart(video.recorded_start);
          if (rawStart == null) return;
          const left = ((rawStart + offsetSec * 1000 - rangeStart) / range) * 100;
          clip.style.left = `${left}%`;
          clip.classList.toggle("has-offset", offsetSec !== 0);
          let chip = clip.querySelector(".timeline-clip-offset");
          if (offsetSec !== 0) {
            if (!chip) {
              chip = document.createElement("span");
              chip.className = "timeline-clip-offset";
              clip.appendChild(chip);
            }
            chip.textContent = fmtOffsetSec(offsetSec);
          } else if (chip) {
            chip.remove();
          }
          const value = $("#timelineOffsetValue");
          const input = $("#timelineOffsetInput");
          if (value) value.textContent = fmtOffsetSec(offsetSec);
          if (input && document.activeElement !== input) input.value = fmtOffsetSec(offsetSec);
          const reset = $("#timelineOffsetReset");
          if (reset) reset.disabled = offsetSec === 0;
        };

        const onMove = (ev) => {
          const dx = ev.clientX - originX;
          if (Math.abs(dx) > 3) moved = true;
          paint(originOffset + pxToSec(dx));
        };
        const onUp = (ev) => {
          clip.releasePointerCapture?.(ev.pointerId);
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", onUp);
          window.removeEventListener("pointercancel", onUp);
          clip.classList.remove("is-dragging");
          if (moved) setClipOffset(video.id, latest, { render: true });
          else {
            updateOffsetChrome();
            renderTimeline();
          }
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        window.addEventListener("pointercancel", onUp);
      });

      clip.addEventListener("click", (event) => {
        if (state.timelineAdjustMode) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        const video = state.index.videos.find((item) => item.id === clip.dataset.id);
        if (!video) return;
        const bounds = clip.getBoundingClientRect();
        const ratio = bounds.width > 0
          ? Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width))
          : 0;
        selectVideo(video.id, ratio * (video.duration_sec || 0));
      });
    });

    if (!canvas || !body || !cursor || !cursorLabel) return;

    const updateCursor = (event) => {
      const track = body.querySelector(".timeline-track") || canvas.querySelector(".timeline-axis-track");
      if (!track) {
        cursor.hidden = true;
        return;
      }
      const trackRect = track.getBoundingClientRect();
      const bodyRect = body.getBoundingClientRect();
      if (
        event.clientX < trackRect.left
        || event.clientX > trackRect.right
        || event.clientY < bodyRect.top
        || event.clientY > bodyRect.bottom
      ) {
        cursor.hidden = true;
        return;
      }
      const ratio = Math.min(1, Math.max(0, (event.clientX - trackRect.left) / Math.max(1, trackRect.width)));
      const ms = rangeStart + ratio * range;
      const x = trackRect.left - bodyRect.left + ratio * trackRect.width;
      cursor.hidden = false;
      cursor.style.setProperty("--cursor-x", `${x}px`);
      cursorLabel.textContent = fmtCameraTime(ms, true);
      cursorLabel.classList.toggle("is-right", ratio > 0.88);
      cursorLabel.classList.toggle("is-left", ratio < 0.12);
    };

    body.addEventListener("mousemove", updateCursor);
    body.addEventListener("mouseleave", () => {
      cursor.hidden = true;
    });
  }

  function updateTimelinePlayhead(video, currentTime) {
    if (!video) return;
    const clip = $(`#timeline .timeline-clip[data-id="${video.id}"]`);
    const progress = clip?.querySelector(".timeline-progress");
    if (!progress) return;
    progress.style.width = `${Math.min(100, Math.max(0, currentTime / Math.max(1, video.duration_sec) * 100))}%`;
  }

  function renderList() {
    updateSearchChrome();
    syncShell();
    renderTimeline();

    const officerMenu = $("#officerMenu");
    const clipMenu = $("#clipMenu");
    const officerValue = $("#officerValue");
    const clipValue = $("#clipValue");
    const clipTrigger = $("#clipTrigger");
    if (!officerMenu || !clipMenu || !officerValue || !clipValue || !clipTrigger) return;

    const narrowing = searching();
    const allGroups = officers();
    const groups = allGroups.filter((o) => !narrowing || o.hitCount > 0);
    const current = state.expandedOfficer;

    if (!groups.length) {
      officerValue.textContent = narrowing
        ? `No officers match “${state.query.trim()}”`
        : "No recordings available";
      officerMenu.innerHTML = "";
      clipTrigger.disabled = true;
      clipValue.textContent = "Select an officer first…";
      clipMenu.innerHTML = "";
      updateLabelChrome(activeMedia());
      return;
    }

    officerValue.textContent = current
      ? current
      : narrowing
        ? "Officers with matches…"
        : "Select officer / source…";

    officerMenu.innerHTML = groups
      .map((o) => {
        const note = narrowing && o.hitCount
          ? `${o.hitCount} hit${o.hitCount === 1 ? "" : "s"}`
          : `${o.videos.length} clip${o.videos.length === 1 ? "" : "s"}`;
        const active = o.name === current ? "is-active" : "";
        return `<button type="button" class="cselect-option ${active}" role="option" data-officer="${escapeHtml(o.name)}" aria-selected="${o.name === current}">
          <span class="cselect-option-main">
            <span class="cselect-option-title">${escapeHtml(o.name)}</span>
            <span class="cselect-option-meta">${note}</span>
          </span>
        </button>`;
      })
      .join("");

    $$("#officerMenu .cselect-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        closeAllSelects();
        selectOfficer(btn.dataset.officer);
      });
    });

    const group =
      groups.find((o) => o.name === current) ||
      allGroups.find((o) => o.name === current);

    if (!group) {
      clipTrigger.disabled = true;
      clipValue.textContent = "Select an officer first…";
      clipMenu.innerHTML = "";
      updateLabelChrome(activeMedia());
      return;
    }

    const videos = narrowing
      ? group.videos.filter((v) => videoHitCount(v) > 0)
      : group.videos;

    clipTrigger.disabled = videos.length === 0;

    const activeVideo = videos.find((v) => v.id === state.activeId);
    if (activeVideo) {
      const hits = videoHitCount(activeVideo);
      clipValue.textContent = `${activeVideo.clipLabel} · ${fmt(activeVideo.duration_sec)}${hits ? ` · ${hits} hit${hits === 1 ? "" : "s"}` : ""}`;
    } else {
      clipValue.textContent = videos.length ? "Select recording…" : "No recordings";
    }

    clipMenu.innerHTML = videos
      .map((v) => {
        const hits = videoHitCount(v);
        const hitNote = hits ? ` · ${hits} hit${hits === 1 ? "" : "s"}` : "";
        const active = v.id === state.activeId ? "is-active" : "";
        const icon = v.kind === "audio" ? "Audio" : "Video";
        return `<button type="button" class="cselect-option ${active}" role="option" data-id="${v.id}" aria-selected="${v.id === state.activeId}">
          <span class="cselect-option-main">
            <span class="cselect-option-title">${escapeHtml(v.clipLabel)} <span class="cselect-kind">${icon}</span></span>
            <span class="cselect-option-meta">${fmt(v.duration_sec)}${hitNote}</span>
          </span>
        </button>`;
      })
      .join("");

    $$("#clipMenu .cselect-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        closeAllSelects();
        selectVideo(btn.dataset.id);
      });
    });

    updateLabelChrome(activeMedia());
  }

  function selectOfficer(name) {
    state.expandedOfficer = name || null;
    state.activeId = null;
    renderList();
    if (!name) return;
    const group = officers().find((o) => o.name === name);
    if (!group) return;
    const visible = searching()
      ? group.videos.filter((v) => videoHitCount(v) > 0)
      : group.videos;
    if (!visible.length) {
      renderTranscript();
      renderVision();
      return;
    }
    const preferred =
      visible.find((v) => videoHitCount(v) > 0) || visible[0];
    selectVideo(preferred.id);
  }

  function renderSegmentPanel(type, selector) {
    const v = activeMedia();
    const box = $(selector);
    const label = type === "transcript" ? "transcript" : "visual analysis";
    if (!v) {
      box.innerHTML = `<div class="empty">Choose a recording to browse its ${label}.</div>`;
      return;
    }
    const segs = v.segments
      .map((seg, i) => ({ seg, i }))
      .filter(({ seg }) => seg.type === type);
    if (!segs.length) {
      box.innerHTML = `<div class="empty">No ${label} is available for this file.</div>`;
      return;
    }
    const prevScroll = box.scrollTop;
    box.innerHTML = segs
      .map(({ seg, i }) => {
        const isHit = scoreSegment(seg, state.query) > 0;
        const ts = type === "transcript"
          ? fmtRange(seg.start, seg.end)
          : fmt(seg.start);
        return `<button type="button" class="seg ${isHit ? "is-hit" : ""}" data-i="${i}" data-start="${seg.start}" data-end="${seg.end ?? ""}">
          <div class="ts">${ts}</div>
          <div>
            <div class="kind ${seg.type}">${seg.type}${isHit ? " · match" : ""}</div>
            <div class="body">${highlight(seg.text, state.query)}</div>
          </div>
        </button>`;
      })
      .join("");
    $$(`${selector} .seg`).forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        seekTo(Number(el.dataset.start), Number(el.dataset.i), { fromUser: true });
      });
    });

    // Keep scroll position across re-renders; only jump to first hit when query changes.
    box.scrollTop = prevScroll;
    if (searching() && box.dataset.hitQuery !== state.query.trim()) {
      box.dataset.hitQuery = state.query.trim();
      const firstHit = box.querySelector(".seg.is-hit");
      if (firstHit) {
        const top = segmentOffsetIn(box, firstHit) - 12;
        box.scrollTo({ top: Math.max(0, top), behavior: "auto" });
      }
    }
    if (!searching()) delete box.dataset.hitQuery;
  }

  function segmentOffsetIn(parent, el) {
    const p = parent.getBoundingClientRect();
    const a = el.getBoundingClientRect();
    return a.top - p.top + parent.scrollTop;
  }

  function renderTranscript() {
    renderSegmentPanel("transcript", "#transcript");
  }

  function renderVision() {
    renderSegmentPanel("scene", "#vision");
  }

  function renderHits() {
    const box = $("#hits");
    if (!searching()) {
      box.innerHTML = `<div class="empty">Type a search term to find spoken words and scene descriptions in the on-screen recording.</div>`;
      return;
    }
    const active = activeMedia();
    if (!active) {
      box.innerHTML = `<div class="empty">Select a recording to see search hits for that officer.</div>`;
      return;
    }
    const hits = collectHitsForOnScreen();
    if (!hits.length) {
      box.innerHTML = `<div class="empty">No matches for “${escapeHtml(state.query.trim())}” in ${escapeHtml(active.officer)}.</div>`;
      return;
    }
    box.innerHTML = hits
      .slice(0, 300)
      .map((h) => {
        const sameClip = h.video.id === active.id;
        const clipNote = sameClip
          ? h.seg.type
          : `${escapeHtml(h.video.clipLabel)} · ${h.seg.type}`;
        return `<button class="seg is-hit" data-vid="${h.video.id}" data-start="${h.seg.start}" data-type="${h.seg.type}">
          <div class="ts">${fmt(h.seg.start)}</div>
          <div>
            <div class="kind ${h.seg.type}">${clipNote}</div>
            <div class="body">${highlight(h.seg.text, state.query)}</div>
          </div>
        </button>`;
      })
      .join("");
    $$("#hits .seg").forEach((el) => {
      el.addEventListener("click", () => {
        const v = state.index.videos.find((x) => x.id === el.dataset.vid);
        if (v) state.expandedOfficer = v.officer;
        selectVideo(el.dataset.vid, Number(el.dataset.start));
        setTab(el.dataset.type === "scene" ? "vision" : "transcript");
      });
    });
  }

  function setTab(tab) {
    state.tab = tab;
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
    $("#transcript").hidden = tab !== "transcript";
    $("#vision").hidden = tab !== "vision";
    $("#hits").hidden = tab !== "hits";
  }

  function seekTo(sec, segIndex, { fromUser = false } = {}) {
    const v = activeMedia();
    if (!v) return;
    const el = mediaEl(v);
    const target = Math.max(0, Number(sec) || 0);

    if (fromUser) {
      const idx = segIndex != null ? segIndex : resolveTranscriptIndex(v, target);
      if (idx != null && idx >= 0) {
        pinnedSegIndex = idx;
        const seg = v.segments[idx];
        pinUntilSec = seg?.end != null ? Number(seg.end) : target + 60;
        segIndex = idx;
      }
      lastFollowedSeg = -1;
    }

    const jump = () => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        $("#clock").textContent = fmt(el.currentTime || target);
        markActiveSegment(segIndex, el.currentTime || target, {
          forceScroll: fromUser,
          pinned: fromUser,
        });
        el.play().catch(() => {});
      };
      if (Math.abs((el.currentTime || 0) - target) < 0.05) {
        finish();
        return;
      }
      el.addEventListener("seeked", finish, { once: true });
      try {
        el.currentTime = target;
      } catch {
        finish();
      }
      // Some browsers skip seeked when metadata is still thin; fall back shortly.
      window.setTimeout(() => {
        if (Math.abs((el.currentTime || 0) - target) < 0.35) finish();
      }, 250);
    };
    if (el.readyState >= 1) jump();
    else el.addEventListener("loadedmetadata", jump, { once: true });
  }

  function resolveTranscriptIndex(v, sec) {
    const transcript = v.segments
      .map((seg, i) => ({ seg, i }))
      .filter(({ seg }) => seg.type === "transcript");
    if (!transcript.length) return -1;

    const containing = transcript.find(({ seg }, i) => {
      const end = seg.end != null
        ? seg.end
        : (transcript[i + 1]?.seg.start ?? Number.POSITIVE_INFINITY);
      return sec >= seg.start && sec < end;
    });
    if (containing) return containing.i;

    // Silence / speech-window gaps: prefer the last spoken segment before this time.
    const prior = transcript.filter(({ seg }) => seg.start <= sec);
    if (prior.length) return prior[prior.length - 1].i;
    return transcript[0].i;
  }

  function markActiveSegment(segIndex, sec, { forceScroll = false, pinned = false } = {}) {
    const v = activeMedia();
    if (!v) return;

    // Drop a user pin once playback leaves that segment's window.
    if (pinnedSegIndex != null && pinUntilSec != null && sec >= pinUntilSec) {
      pinnedSegIndex = null;
      pinUntilSec = null;
    }

    let idx = segIndex;
    if (idx == null || v.segments[idx]?.type !== "transcript") {
      // While pinned to a user click, keep that highlight until pin expires
      // so early timeupdate events during seek don't yank selection away.
      if (pinnedSegIndex != null && v.segments[pinnedSegIndex]?.type === "transcript") {
        idx = pinnedSegIndex;
      } else {
        idx = resolveTranscriptIndex(v, sec);
      }
    }

    $$("#transcript .seg").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.i) === idx);
    });

    const parent = $("#transcript");
    if (!parent || state.tab !== "transcript" || idx < 0) return;

    const shouldFollow = forceScroll || pinned || pinnedSegIndex == null;
    if (!shouldFollow) return;

    if (!forceScroll && idx === lastFollowedSeg) return;
    lastFollowedSeg = idx;

    const active = $(`#transcript .seg[data-i="${idx}"]`);
    if (!active) return;

    const top = segmentOffsetIn(parent, active);
    const bottom = top + active.offsetHeight;
    const viewTop = parent.scrollTop;
    const viewBottom = viewTop + parent.clientHeight;
    const margin = 48;

    if (!forceScroll && top >= viewTop + margin && bottom <= viewBottom - margin) return;

    parent.scrollTo({
      top: Math.max(0, top - Math.min(72, parent.clientHeight / 4)),
      behavior: "auto",
    });
  }

  function safeFilename(name) {
    return String(name || "recording")
      .replace(/[^\w.\- ]+/g, "")
      .replace(/\s+/g, "_")
      .replace(/_+/g, "_")
      .slice(0, 120);
  }

  function segmentsAsText(v, type) {
    const label = type === "scene" ? "Visual analysis" : "Transcript";
    const lines = [
      `${v.officer} — ${v.clipLabel}`,
      v.filename,
      `${label} — Washington v Jersey City`,
      "",
      "IMPORTANT: This content was generated by AI and may contain errors or omissions.",
      "Please review and verify it against the original recording before relying on it.",
      "",
    ];
    const segs = v.segments.filter((s) => s.type === type);
    if (!segs.length) {
      lines.push(`(No ${label.toLowerCase()} available for this recording.)`);
      return lines.join("\n");
    }
    for (const seg of segs) {
      lines.push(`[${fmt(seg.start)}]`);
      lines.push(seg.text.trim());
      lines.push("");
    }
    return lines.join("\n").trim() + "\n";
  }

  function triggerTextDownload(filename, text) {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function updateDownloads(v) {
    const disclaimer = $("#aiDisclaimer");
    const dlMedia = $("#dlMedia");
    const dlTranscript = $("#dlTranscript");
    const dlVision = $("#dlVision");
    if (!v) {
      dlMedia.hidden = true;
      dlTranscript.hidden = true;
      dlVision.hidden = true;
      disclaimer.hidden = true;
      return;
    }
    disclaimer.hidden = false;
    dlMedia.hidden = false;
    dlTranscript.hidden = false;
    dlVision.hidden = false;
    const stem = safeFilename(`${v.officer}_${v.clipLabel}`);
    const mediaName = v.filename || `${stem}.${v.kind === "audio" ? "wav" : "mp4"}`;
    const sep = v.media.includes("?") ? "&" : "?";
    dlMedia.href = `${v.media}${sep}download=1`;
    dlMedia.download = mediaName;
    dlMedia.title = v.kind === "audio" ? "Download audio" : "Download video";
    dlMedia.setAttribute("aria-label", dlMedia.title);
  }

  function selectVideo(id, seekSec) {
    const v = state.index.videos.find((x) => x.id === id);
    if (!v) return;
    const switching = state.activeId !== id;
    state.activeId = id;
    state.expandedOfficer = v.officer;
    const recorded = parseRecordedStart(v.recorded_start);
    if (recorded != null) state.timelineDay = cameraDayKey(recorded);
    lastFollowedSeg = -1;
    pinnedSegIndex = null;
    pinUntilSec = null;
    const transcriptBox = $("#transcript");
    if (transcriptBox && switching) {
      delete transcriptBox.dataset.hitQuery;
      transcriptBox.scrollTo({ top: 0, behavior: "auto" });
    }
    $("#activeTitle").textContent = `${v.officer} — ${v.clipLabel}`;
    $("#dur").textContent = fmt(v.duration_sec);
    updateDownloads(v);
    updateLabelChrome(v);

    const el = mediaEl(v);
    if (switching) {
      player.pause();
      audio.pause();
      player.removeAttribute("src");
      audio.removeAttribute("src");
      player.hidden = v.kind === "audio";
      audio.hidden = v.kind !== "audio";
      el.src = v.media;
      el.load();
    } else {
      player.hidden = v.kind === "audio";
      audio.hidden = v.kind !== "audio";
    }

    renderList();
    renderTranscript();
    renderVision();
    renderHits();
    if (typeof seekSec === "number") {
      setTab("transcript");
      // Defer so the freshly rendered transcript nodes are measurable.
      requestAnimationFrame(() => {
        seekTo(seekSec, null, { fromUser: true });
      });
    } else if (searching()) {
      setTab("hits");
    }
  }

  function tick() {
    const v = activeMedia();
    if (!v) return;
    const el = mediaEl(v);
    if (!el || (el.paused && el.currentTime === 0)) return;
    $("#clock").textContent = fmt(el.currentTime);
    updateTimelinePlayhead(v, el.currentTime);
    if (!el.paused) markActiveSegment(null, el.currentTime);
  }

  function onSearchInput() {
    state.query = $("#q").value;
    renderList();
    renderTranscript();
    renderVision();
    renderHits();
    if (searching()) setTab("hits");
  }

  function caseId() {
    if (window.__BWC_CASE_ID__ != null && Number.isFinite(Number(window.__BWC_CASE_ID__))) {
      return Number(window.__BWC_CASE_ID__);
    }
    return state.index && state.index.case_id != null ? Number(state.index.case_id) : null;
  }

  function hasSelectedSource() {
    const name = state.expandedOfficer;
    if (!name) return false;
    return officers().some((o) => o.name === name);
  }

  function setPenEnabled(pen, enabled, title) {
    if (!pen) return;
    pen.hidden = false;
    pen.disabled = !enabled;
    pen.setAttribute("aria-disabled", enabled ? "false" : "true");
    pen.title = enabled ? (title || "Rename Officer / Source") : "Select an officer / source first";
  }

  function updateLabelChrome(v) {
    const sourceReady = hasSelectedSource();
    const videoReady = Boolean(v);
    setPenEnabled($("#renameOfficerPen"), sourceReady);
    setPenEnabled(
      $("#renameTitlePen"),
      videoReady,
      videoReady ? "Rename Officer / Source" : "Select a recording first",
    );
  }

  function closeLabelMenu() {
    const combo = $("#labelCombo");
    const menu = $("#labelMenu");
    const input = $("#labelInput");
    if (combo) combo.classList.remove("open");
    if (menu) menu.hidden = true;
    if (input) input.setAttribute("aria-expanded", "false");
    labelMenuActive = -1;
    labelMenuQuery = "";
  }

  function closeRenameModal() {
    closeLabelMenu();
    const modal = $("#renameModal");
    if (modal) modal.hidden = true;
    const status = $("#labelStatus");
    if (status) {
      status.hidden = true;
      status.textContent = "";
    }
  }

  function allLabelChoices() {
    const seen = new Set();
    const out = [];
    const add = (label, type) => {
      const name = String(label || "").trim();
      if (!name) return;
      const key = name.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push({ label: name, type: type || "" });
    };
    for (const s of labelSuggestions) add(s.label, s.type);
    for (const o of officers()) add(o.name, "current");
    out.sort((a, b) => a.label.localeCompare(b.label));
    return out;
  }

  function filteredLabelChoices(query) {
    const q = String(query || "").trim().toLowerCase();
    const all = allLabelChoices();
    if (!q) return all;
    return all.filter((s) => s.label.toLowerCase().includes(q));
  }

  function renderLabelMenu(query) {
    const menu = $("#labelMenu");
    if (!menu) return;
    const items = filteredLabelChoices(query);
    if (!items.length) {
      menu.innerHTML = `<div class="label-combo-empty">No matches — keep typing a custom name.</div>`;
      labelMenuActive = -1;
      return;
    }
    if (labelMenuActive >= items.length) labelMenuActive = items.length - 1;
    menu.innerHTML = items
      .map((s, i) => {
        const active = i === labelMenuActive ? "is-active" : "";
        const meta = s.type && s.type !== "current"
          ? escapeHtml(s.type)
          : (s.type === "current" ? "in case" : "");
        return `<button type="button" class="label-combo-option ${active}" role="option" data-label="${escapeHtml(s.label)}" aria-selected="${i === labelMenuActive}">
          <span class="label-combo-option-title">${escapeHtml(s.label)}</span>
          ${meta ? `<span class="label-combo-option-meta">${meta}</span>` : ""}
        </button>`;
      })
      .join("");
    $$(".label-combo-option", menu).forEach((btn) => {
      btn.addEventListener("mousedown", (e) => {
        e.preventDefault();
        pickLabelSuggestion(btn.dataset.label);
      });
    });
  }

  function openLabelMenu({ selectText = false, filter = false } = {}) {
    const combo = $("#labelCombo");
    const menu = $("#labelMenu");
    const input = $("#labelInput");
    if (!combo || !menu || !input) return;
    // Click/chevron on a filled name should show the full entity list; filter only while typing.
    labelMenuQuery = filter ? input.value : "";
    renderLabelMenu(labelMenuQuery);
    combo.classList.add("open");
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
    // Prevent the same click that opened the menu from immediately closing it.
    labelMenuIgnoreDocClick = true;
    setTimeout(() => { labelMenuIgnoreDocClick = false; }, 0);
    if (selectText) input.select();
  }

  function pickLabelSuggestion(label) {
    const input = $("#labelInput");
    if (!input) return;
    input.value = label || "";
    closeLabelMenu();
    input.focus();
  }

  async function ensureLabelSuggestions() {
    if (labelSuggestionsLoaded) return;
    const id = caseId();
    const url = "/api/bwc/label-suggestions" + (id != null ? `?case_id=${encodeURIComponent(id)}` : "");
    try {
      const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      labelSuggestions = (data.suggestions || [])
        .map((s) => ({ label: String(s.label || "").trim(), type: String(s.type || "").trim() }))
        .filter((s) => s.label);
      labelSuggestionsLoaded = true;
    } catch {
      /* suggestions are optional */
    }
  }

  function renameTargetVideo() {
    let v = activeMedia();
    if (v) return v;
    if (!hasSelectedSource()) return null;
    const group = officers().find((o) => o.name === state.expandedOfficer);
    return group && group.videos && group.videos.length ? group.videos[0] : null;
  }

  async function openRenameModal() {
    const v = renameTargetVideo();
    const modal = $("#renameModal");
    const input = $("#labelInput");
    if (!v || !modal || !input) return;
    closeAllSelects();
    await ensureLabelSuggestions();
    input.value = v.officer || state.expandedOfficer || "";
    const cameraRadio = modal.querySelector('input[name="labelScope"][value="camera"]');
    if (cameraRadio) cameraRadio.checked = true;
    const status = $("#labelStatus");
    if (status) {
      status.hidden = true;
      status.textContent = "";
    }
    modal.hidden = false;
    requestAnimationFrame(() => {
      input.focus();
      openLabelMenu({ selectText: true });
    });
  }

  function selectedLabelScope() {
    const el = document.querySelector('input[name="labelScope"]:checked');
    return el && el.value === "clip" ? "clip" : "camera";
  }

  async function saveLabel({ clear = false } = {}) {
    const v = renameTargetVideo();
    if (!v || labelBusy) return;
    const input = $("#labelInput");
    const status = $("#labelStatus");
    const label = clear ? "" : String(input && input.value || "").trim();
    if (!clear && !label) {
      if (status) {
        status.hidden = false;
        status.textContent = "Enter a name, or Clear to restore the device label.";
      }
      return;
    }
    labelBusy = true;
    if (status) {
      status.hidden = false;
      status.textContent = "Saving…";
    }
    try {
      const res = await fetch("/api/bwc/label", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          case_id: caseId(),
          file_id: v.file_id,
          label: clear ? null : label,
          scope: selectedLabelScope(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      if (data.index) {
        state.index = data.index;
        enrichVideos();
      }
      const keepId = v.id;
      const el = mediaEl(v);
      const keepTime = el && Number.isFinite(el.currentTime) ? el.currentTime : 0;
      closeRenameModal();
      renderList();
      if (typeof renderTimeline === "function") renderTimeline();
      selectVideo(keepId);
      if (keepTime > 0.25) {
        const next = activeMedia();
        const media = next ? mediaEl(next) : null;
        if (media) {
          const restore = () => {
            try { media.currentTime = keepTime; } catch { /* ignore */ }
          };
          if (media.readyState >= 1) restore();
          else media.addEventListener("loadedmetadata", restore, { once: true });
        }
      }
    } catch (err) {
      if (status) {
        status.hidden = false;
        status.textContent = err.message || String(err);
      }
    } finally {
      labelBusy = false;
    }
  }

  function wire() {
    if (wired) return;
    wired = true;
    $("#q").addEventListener("input", onSearchInput);
    $("#clearSearch").addEventListener("click", () => {
      clearSearch();
      $("#q").focus();
    });
    $("#officerTrigger").addEventListener("click", (e) => {
      e.stopPropagation();
      openSelect("officer");
    });
    $("#clipTrigger").addEventListener("click", (e) => {
      e.stopPropagation();
      openSelect("clip");
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".cselect")) closeAllSelects();
      if (!e.target.closest(".timeline-filter")) closeTimelineFilter();
      if (labelMenuIgnoreDocClick) return;
      if (!e.target.closest(".label-combo")) closeLabelMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        const combo = $("#labelCombo");
        if (combo && combo.classList.contains("open")) {
          closeLabelMenu();
          return;
        }
        closeAllSelects();
        closeTimelineFilter();
        closeRenameModal();
      }
    });
    ["renameOfficerPen", "renameTitlePen"].forEach((id) => {
      const pen = document.getElementById(id);
      if (!pen) return;
      pen.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (pen.disabled) return;
        openRenameModal();
      });
    });
    const renameModal = $("#renameModal");
    if (renameModal) {
      renameModal.addEventListener("click", (e) => {
        if (e.target && e.target.closest && e.target.closest("[data-rename-dismiss]")) {
          closeRenameModal();
        }
      });
    }
    const labelSave = $("#labelSave");
    if (labelSave) labelSave.addEventListener("click", () => saveLabel());
    const labelClear = $("#labelClear");
    if (labelClear) labelClear.addEventListener("click", () => saveLabel({ clear: true }));
    const labelInput = $("#labelInput");
    if (labelInput) {
      labelInput.addEventListener("click", (e) => {
        e.stopPropagation();
        openLabelMenu();
      });
      labelInput.addEventListener("input", () => {
        labelMenuActive = -1;
        openLabelMenu({ filter: true });
      });
      labelInput.addEventListener("keydown", (e) => {
        const menu = $("#labelMenu");
        const open = menu && !menu.hidden;
        if (e.key === "ArrowDown") {
          e.preventDefault();
          if (!open) openLabelMenu();
          const opts = $$(".label-combo-option", $("#labelMenu"));
          if (!opts.length) return;
          labelMenuActive = Math.min(opts.length - 1, labelMenuActive + 1);
          if (labelMenuActive < 0) labelMenuActive = 0;
          renderLabelMenu(labelMenuQuery);
          const active = $$(".label-combo-option", $("#labelMenu"))[labelMenuActive];
          if (active) active.scrollIntoView({ block: "nearest" });
          return;
        }
        if (e.key === "ArrowUp" && open) {
          e.preventDefault();
          const opts = $$(".label-combo-option", menu);
          if (!opts.length) return;
          labelMenuActive = Math.max(0, labelMenuActive - 1);
          renderLabelMenu(labelMenuQuery);
          const active = $$(".label-combo-option", $("#labelMenu"))[labelMenuActive];
          if (active) active.scrollIntoView({ block: "nearest" });
          return;
        }
        if (e.key === "Enter") {
          e.preventDefault();
          const items = open ? $$(".label-combo-option", menu) : [];
          if (open && labelMenuActive >= 0 && items[labelMenuActive]) {
            pickLabelSuggestion(items[labelMenuActive].dataset.label);
            return;
          }
          closeLabelMenu();
          saveLabel();
          return;
        }
        if (e.key === "Escape" && open) {
          e.preventDefault();
          e.stopPropagation();
          closeLabelMenu();
        }
      });
    }
    const labelToggle = $("#labelComboToggle");
    if (labelToggle) {
      // mousedown so we win over input blur/focus races and always show the entity list.
      labelToggle.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
      labelToggle.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const combo = $("#labelCombo");
        const wasOpen = combo && combo.classList.contains("open") && $("#labelMenu") && !$("#labelMenu").hidden;
        if (wasOpen) {
          closeLabelMenu();
          return;
        }
        openLabelMenu();
        const input = $("#labelInput");
        if (input) input.focus();
      });
    }
    $("#dlTranscript").addEventListener("click", (e) => {
      e.preventDefault();
      const v = activeMedia();
      if (!v) return;
      const stem = safeFilename(`${v.officer}_${v.clipLabel}`);
      triggerTextDownload(`${stem}_transcript.txt`, segmentsAsText(v, "transcript"));
    });
    $("#dlVision").addEventListener("click", (e) => {
      e.preventDefault();
      const v = activeMedia();
      if (!v) return;
      const stem = safeFilename(`${v.officer}_${v.clipLabel}`);
      triggerTextDownload(`${stem}_visual_analysis.txt`, segmentsAsText(v, "scene"));
    });
    $$(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        setTab(btn.dataset.tab);
        if (btn.dataset.tab === "hits") renderHits();
      });
    });
    player.addEventListener("timeupdate", tick);
    audio.addEventListener("timeupdate", tick);
    player.addEventListener("loadedmetadata", () => {
      $("#dur").textContent = fmt(player.duration || activeMedia()?.duration_sec || 0);
    });
    audio.addEventListener("loadedmetadata", () => {
      $("#dur").textContent = fmt(audio.duration || activeMedia()?.duration_sec || 0);
    });
  }

  async function boot(generation) {
    player = $("#player");
    audio = $("#audio");
    if (!player || !audio || !$("#officerSelect")) {
      throw new Error("BWC viewer DOM is not ready");
    }

    // Fresh instance each mount — allow re-wiring new nodes.
    wired = false;
    state.index = null;
    state.activeId = null;
    state.expandedOfficer = null;
    state.query = "";
    state.tab = "transcript";
    state.timelineExpanded = false;

    wire();
    const indexUrl =
      window.__BWC_INDEX_URL__ || `data/index.json?v=${Date.now()}`;
    const res = await fetch(indexUrl, { cache: "no-store", credentials: "same-origin" });
    if (!res.ok) throw new Error("Could not load BWC index");
    if (generation !== bootGeneration) return;

    state.index = await res.json();
    enrichVideos();
    loadTimelineOffsets();
    if (generation !== bootGeneration) return;

    const subtitle = $("#subtitle");
    if (subtitle) subtitle.textContent = state.index.title;
    renderList();
    renderTranscript();
    renderVision();
    renderHits();

    applyDeepLink();
  }

  function applyDeepLink() {
    const dl = window.__BWC_DEEPLINK__;
    if (!dl || !dl.videoId) return;
    window.__BWC_DEEPLINK__ = null;
    const videos = (state.index && state.index.videos) || [];
    const v = videos.find((x) => x.id === dl.videoId);
    if (!v) return;
    const seek = typeof dl.t === "number" && dl.t > 0 ? dl.t : undefined;
    selectVideo(v.id, seek);
  }

  window.startBwcViewer = function startBwcViewer() {
    const generation = ++bootGeneration;
    return boot(generation).catch((err) => {
      if (generation !== bootGeneration) return;
      const shell = document.querySelector(".bwc-shell") || document.body;
      shell.innerHTML = `<div class="empty" style="padding:3rem">Failed to load BWC viewer: ${escapeHtml(err.message)}</div>`;
    });
  };
})();
