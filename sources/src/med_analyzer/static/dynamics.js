// Dynamics page: pick analytes, draw one "% of norm" chart and a comparison table.
//
// All text that comes from the server (analyte names, units, reference text)
// is put into the page with textContent or as ECharts text, never as markup.
(() => {
  'use strict';

  const MAX_SELECTED = 12;
  const DEBOUNCE_MS = 250;
  const Y_MIN = -100;
  const Y_MAX = 300;
  // Okabe-Ito based and without red, which is reserved for out-of-range points.
  const PALETTE = [
    '#0072b2', '#e69f00', '#009e73', '#6a3d9a',
    '#56b4e9', '#cc79a7', '#8c564b', '#666666',
  ];
  const OUT_COLOR = '#d93025';
  const NORM_BAND_COLOR = 'rgba(46, 160, 67, 0.14)';
  const NO_PERCENT = 'нет процентной шкалы (односторонняя норма)';
  const LOAD_FAILED = 'Не удалось загрузить данные, попробуйте ещё раз';
  const TOO_MANY = `Можно выбрать не больше ${MAX_SELECTED} показателей`;
  const DASH = '—';

  const state = {
    selected: [], // analyte ids in the order of choosing
    options: [], // analytes of the current search, for redrawing the marks
    names: new Map(), // analyte id -> name, for the chips
    series: new Map(), // analyte id -> series of the last answer
    chart: null,
    requests: 0, // guards against an old answer overwriting a newer one
  };

  const byId = (id) => document.getElementById(id);

  // ---- formatting -----------------------------------------------------------

  const comma = (text) => String(text).replace('.', ',');

  function formatPercent(percent) {
    return `${comma(percent.toFixed(1))} %`;
  }

  function formatDate(iso) {
    const [year, month, day] = iso.split('-');
    return `${day}.${month}.${year}`;
  }

  function decimals(text) {
    const dot = text.indexOf('.');
    return dot < 0 ? 0 : text.length - dot - 1;
  }

  function formatDelta(delta, currentText, previousText) {
    if (delta === null) {
      return DASH;
    }
    if (delta === 0) {
      return '0';
    }
    const digits = Math.max(decimals(currentText), decimals(previousText));
    const sign = delta > 0 ? '+' : '−';
    const arrow = delta > 0 ? '▲' : '▼';
    return `${arrow} ${sign}${comma(Math.abs(delta).toFixed(digits))}`;
  }

  function formatReference(item) {
    const { low, high } = item;
    if (low !== null && high !== null) {
      return `${comma(low)}–${comma(high)}`;
    }
    if (high !== null) {
      return `до ${comma(high)}`;
    }
    if (low !== null) {
      return `от ${comma(low)}`;
    }
    return item.ref_raw || DASH;
  }

  function withUnit(text, unit) {
    return unit ? `${text} ${unit}` : text;
  }

  function hasPercent(series) {
    return series.points.some((point) => point.percent !== null);
  }

  // ---- URL ------------------------------------------------------------------

  function readSelection() {
    const params = new URLSearchParams(window.location.search);
    const ids = [];
    for (const raw of params.getAll('a')) {
      const id = Number(raw);
      if (Number.isSafeInteger(id) && id > 0 && !ids.includes(id)) {
        ids.push(id);
      }
    }
    return ids.slice(0, MAX_SELECTED);
  }

  function writeSelection() {
    const url = new URL(window.location.href);
    url.searchParams.delete('a');
    for (const id of state.selected) {
      url.searchParams.append('a', String(id));
    }
    window.history.replaceState(null, '', url);
  }

  // ---- server ---------------------------------------------------------------

  async function fetchJson(url) {
    const response = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  }

  function showError(message) {
    const box = byId('dyn-error');
    box.textContent = message || '';
    box.hidden = !message;
  }

  // ---- picker ---------------------------------------------------------------

  function renderOptions(analytes) {
    state.options = analytes;
    const list = byId('dyn-options');
    list.replaceChildren();
    for (const analyte of analytes) {
      state.names.set(analyte.id, analyte.name);
      const item = document.createElement('li');
      const button = document.createElement('button');
      const chosen = state.selected.includes(analyte.id);
      button.type = 'button';
      button.className = chosen ? 'option selected' : 'option';
      button.setAttribute('aria-pressed', String(chosen));
      button.textContent = `${analyte.name} (${analyte.count})`;
      button.addEventListener('click', () => toggle(analyte.id));
      item.append(button);
      list.append(item);
    }
    list.hidden = analytes.length === 0;
  }

  async function loadOptions(query) {
    const url = `/api/analytes?q=${encodeURIComponent(query)}`;
    try {
      const analytes = await fetchJson(url);
      showError('');
      renderOptions(analytes);
      return analytes;
    } catch (error) {
      showError(LOAD_FAILED);
      return null;
    }
  }

  function renderChips() {
    const list = byId('dyn-chips');
    list.replaceChildren();
    state.selected.forEach((id, index) => {
      const chip = document.createElement('li');
      chip.className = 'chip';
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.backgroundColor = PALETTE[index % PALETTE.length];
      const label = document.createElement('span');
      label.textContent = state.names.get(id) || `#${id}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'chip-remove';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `Убрать: ${label.textContent}`);
      remove.addEventListener('click', () => toggle(id));
      chip.append(swatch, label);
      const series = state.series.get(id);
      if (series && !hasPercent(series)) {
        const note = document.createElement('span');
        note.className = 'note';
        note.textContent = NO_PERCENT;
        chip.append(note);
      }
      chip.append(remove);
      list.append(chip);
    });
  }

  function toggle(id) {
    const position = state.selected.indexOf(id);
    if (position >= 0) {
      state.selected.splice(position, 1);
      showError('');
    } else if (state.selected.length >= MAX_SELECTED) {
      showError(TOO_MANY);
      return;
    } else {
      state.selected.push(id);
    }
    writeSelection();
    refreshOptionMarks();
    loadDynamics();
  }

  function refreshOptionMarks() {
    renderOptions(state.options);
  }

  // ---- chart ----------------------------------------------------------------

  function ensureChart() {
    const container = byId('dyn-chart');
    container.hidden = false;
    if (state.chart === null) {
      state.chart = window.echarts.init(container);
    } else {
      state.chart.resize();
    }
    return state.chart;
  }

  function hideChart() {
    byId('dyn-chart').hidden = true;
  }

  function chartPoint(series, point) {
    const clamped = Math.min(Y_MAX, Math.max(Y_MIN, point.percent));
    const offAxis = clamped !== point.percent;
    const item = {
      value: [point.date, clamped],
      point,
      analyte: series.name,
    };
    if (offAxis) {
      item.symbol = 'triangle';
      item.symbolSize = 11;
      // Points the way the real value lies beyond the axis.
      item.symbolRotate = point.percent > Y_MAX ? 0 : 180;
    } else if (point.out === true) {
      item.symbol = 'diamond';
      item.symbolSize = 11;
    }
    if (point.out === true) {
      item.itemStyle = { color: OUT_COLOR };
    }
    return item;
  }

  function chartSeries(series, index) {
    const color = PALETTE[index % PALETTE.length];
    return {
      name: series.name,
      type: 'line',
      symbol: 'circle',
      symbolSize: 7,
      color,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      data: series.points
        .filter((point) => point.percent !== null)
        .map((point) => chartPoint(series, point)),
    };
  }

  function normBand() {
    // An empty series that only carries the shaded 0-100 % band.
    return {
      type: 'line',
      silent: true,
      data: [],
      markArea: {
        silent: true,
        itemStyle: { color: NORM_BAND_COLOR },
        data: [[{ yAxis: 0 }, { yAxis: 100 }]],
      },
    };
  }

  function line(parent, text, className) {
    const row = document.createElement('div');
    row.textContent = text;
    if (className) {
      row.className = className;
    }
    parent.append(row);
  }

  // ECharts accepts a DOM node as tooltip content, so no markup is built.
  function tooltipNode(params) {
    const { point, analyte } = params.data;
    const node = document.createElement('div');
    line(node, analyte, 'tip-title');
    line(node, formatDate(point.date));
    line(node, withUnit(comma(point.value_str), point.unit));
    line(node, `Норма: ${formatReference(point)}`);
    line(node, `${formatPercent(point.percent)} от нормы`);
    if (point.out === true) {
      line(node, 'Вне нормы', 'tip-out');
    }
    return node;
  }

  function buildOption(seriesList) {
    return {
      animation: false,
      grid: { left: 56, right: 24, top: 32, bottom: 40 },
      tooltip: { trigger: 'item', formatter: tooltipNode },
      xAxis: {
        type: 'time',
        axisLabel: {
          hideOverlap: true,
          // Local getters: ECharts reads a plain date as local midnight.
          formatter: (value) => {
            const day = new Date(value);
            const pad = (number) => String(number).padStart(2, '0');
            return `${pad(day.getDate())}.${pad(day.getMonth() + 1)}.${day.getFullYear()}`;
          },
        },
      },
      yAxis: {
        type: 'value',
        name: '% нормы',
        min: Y_MIN,
        max: Y_MAX,
        interval: 100,
        splitLine: { lineStyle: { color: '#e6e8ee' } },
      },
      series: [normBand(), ...seriesList.map(chartSeries)],
    };
  }

  // ---- table ----------------------------------------------------------------

  function cell(row, text, className) {
    const td = document.createElement('td');
    if (className) {
      td.className = className;
    }
    td.textContent = text;
    row.append(td);
    return td;
  }

  function previousUnit(series) {
    const { points } = series;
    return points.length > 1 ? points[points.length - 2].unit : null;
  }

  function renderTable(rows) {
    const body = byId('dyn-rows');
    body.replaceChildren();
    for (const item of rows) {
      const series = state.series.get(item.analyte_id);
      const tr = document.createElement('tr');
      if (item.out === true) {
        tr.className = 'row-out';
      }
      const name = cell(tr, item.name);
      if (series && !hasPercent(series)) {
        const note = document.createElement('div');
        note.className = 'note';
        note.textContent = NO_PERCENT;
        name.append(note);
      }
      const now = cell(tr, withUnit(comma(item.current_str), item.unit), 'value');
      const when = document.createElement('div');
      when.className = 'muted';
      when.textContent = formatDate(item.date);
      now.append(when);
      cell(tr, formatReference(item));
      if (item.previous_str === null) {
        cell(tr, DASH);
      } else {
        const before = cell(
          tr,
          withUnit(comma(item.previous_str), previousUnit(series)),
        );
        const prior = document.createElement('div');
        prior.className = 'muted';
        prior.textContent = formatDate(item.previous_date);
        before.append(prior);
      }
      cell(
        tr,
        item.previous_str === null
          ? DASH
          : formatDelta(item.delta, item.current_str, item.previous_str),
      );
      body.append(tr);
    }
  }

  // ---- page -----------------------------------------------------------------

  function render(answer) {
    state.series = new Map(answer.series.map((item) => [item.analyte_id, item]));
    for (const item of answer.series) {
      state.names.set(item.analyte_id, item.name);
    }
    // Ids the server does not know (stale link) are dropped from the choice.
    const known = state.selected.filter((id) => state.series.has(id));
    if (known.length !== state.selected.length) {
      state.selected = known;
      writeSelection();
    }
    renderChips();
    const drawable = answer.series.filter(hasPercent);
    const empty = answer.series.length === 0;
    byId('dyn-hint').hidden = !empty;
    byId('dyn-legend').hidden = drawable.length === 0;
    byId('dyn-table').hidden = empty;
    renderTable(answer.table);
    if (drawable.length === 0) {
      hideChart();
      return;
    }
    // Colours follow the chosen order, also when a series has no percent.
    const chart = ensureChart();
    chart.setOption(buildOption(answer.series), { notMerge: true });
  }

  async function loadDynamics() {
    const request = ++state.requests;
    renderChips();
    if (state.selected.length === 0) {
      state.series = new Map();
      render({ series: [], table: [] });
      return;
    }
    const query = state.selected.map((id) => `analyte_id=${id}`).join('&');
    try {
      const answer = await fetchJson(`/api/dynamics?${query}`);
      if (request !== state.requests) {
        return;
      }
      showError('');
      render(answer);
    } catch (error) {
      if (request === state.requests) {
        showError(LOAD_FAILED);
      }
    }
  }

  function onSearch() {
    let timer = null;
    return () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(
        () => loadOptions(byId('dyn-search').value.trim()),
        DEBOUNCE_MS,
      );
    };
  }

  async function init() {
    state.selected = readSelection();
    const analytes = await loadOptions('');
    if (analytes !== null && analytes.length === 0) {
      byId('dyn-empty-data').hidden = false;
      byId('dyn-picker').hidden = true;
      byId('dyn-hint').hidden = true;
      return;
    }
    byId('dyn-search').addEventListener('input', onSearch());
    window.addEventListener('resize', () => {
      if (state.chart !== null) {
        state.chart.resize();
      }
    });
    loadDynamics();
  }

  init();
})();
