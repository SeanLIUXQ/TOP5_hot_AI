(() => {
  'use strict';

  const readJSON = id => {
    const element = document.getElementById(id);
    return element ? JSON.parse(element.textContent) : null;
  };

  function syncLanguageLink() {
    const link = document.getElementById('alternate-language');
    if (!link) return;
    const url = new URL(link.href);
    url.search = location.search;
    url.hash = location.hash;
    link.href = url.href;
  }

  function updateQuery(values, replace = false) {
    const url = new URL(location.href);
    for (const [key, value] of Object.entries(values)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    if (url.href !== location.href) {
      if (replace) history.replaceState(null, '', url);
      else history.pushState(null, '', url);
    }
    syncLanguageLink();
  }

  function bindRanking(ranking) {
    const form = ranking.querySelector('[data-filter-form]');
    if (!form) return;
    const select = form.elements.language;
    const rows = [...ranking.querySelectorAll('tr[data-language]')];
    const initial = form.dataset.initial;
    const serverFiltered = initial && rows.every(row => row.dataset.language === initial);

    function filter(language) {
      if (language && ![...select.options].some(option => option.value === language)) {
        select.add(new Option(language, language));
      }
      select.value = language;
      let count = 0;
      for (const row of rows) {
        row.hidden = Boolean(language) && row.dataset.language !== language;
        if (!row.hidden) count++;
      }
      ranking.querySelector('[data-match-count]').textContent = count;
      ranking.querySelector('[data-no-matches]').hidden = count > 0;
      ranking.querySelector('table').hidden = !count;
    }

    function apply() {
      const language = select.value;
      if (serverFiltered && language !== initial) {
        // A server-rendered query may contain only the selected language's rows.
        form.submit();
        return;
      }
      filter(language);
      updateQuery({language: language || null});
    }

    const restore = () => filter(new URLSearchParams(location.search).get('language') ?? initial);
    form.querySelector('[data-apply]').hidden = true;
    form.addEventListener('submit', event => { event.preventDefault(); apply(); });
    select.addEventListener('change', apply);
    window.addEventListener('popstate', restore);
    restore();
  }

  function bindInfra(form) {
    const search = form.elements.q;
    const watchlist = form.elements.watchlist;
    const recent = form.elements.recent;
    const clear = form.querySelector('[data-clear-infra]');
    const layout = document.querySelector('.infra-layout');
    const counter = document.querySelector('[data-infra-count]');
    const normalise = value => value.normalize('NFKC').toLowerCase().trim();
    // Index the server-rendered entries once. Filtering performs no requests.
    const groups = [...layout.querySelectorAll('[data-category]')].map(section => {
      const category = section.dataset.category;
      const link = layout.querySelector('[data-category-link="' + category + '"]');
      const label = category + ' ' + section.querySelector('h2').textContent;
      const projects = [...section.querySelectorAll('.infra-project')].map(article => ({
        article, watchlisted: article.dataset.watchlisted === '1', recent: article.hasAttribute('data-recent'),
        text: normalise(label + ' ' + article.textContent)
      }));
      return {section, link, count: link.querySelector('.item-count'), projects};
    });

    function apply() {
      const terms = normalise(search.value).split(/\s+/).filter(Boolean);
      let total = 0;
      for (const group of groups) {
        let count = 0;
        for (const project of group.projects) {
          const matches = (!watchlist.checked || project.watchlisted) && (!recent?.checked || project.recent)
            && terms.every(term => project.text.includes(term));
          project.article.hidden = !matches;
          if (matches) count++;
        }
        group.count.textContent = count;
        group.link.hidden = group.section.hidden = count === 0;
        total += count;
      }
      clear.hidden = !search.value && !watchlist.checked && !recent?.checked;
      counter.textContent = total;
      counter.parentElement.hidden = clear.hidden;
      layout.hidden = !total;
      document.querySelector('[data-infra-empty]').hidden = total > 0;
    }

    const persist = () => updateQuery({
      q: search.value.trim() || null, watchlist: watchlist.checked ? '1' : null, recent: recent?.checked ? '1' : null
    }, true);
    let timer;
    const changed = () => {
      apply();
      clearTimeout(timer);
      timer = setTimeout(persist, 150);
    };
    function restore() {
      clearTimeout(timer);
      const params = new URLSearchParams(location.search);
      search.value = params.get('q') || '';
      watchlist.checked = params.get('watchlist') === '1';
      if (recent) recent.checked = params.get('recent') === '1';
      apply();
    }
    search.addEventListener('input', changed);
    watchlist.addEventListener('change', changed);
    recent?.addEventListener('change', changed);
    clear.addEventListener('click', () => {
      form.reset();
      changed();
      search.focus();
    });
    form.addEventListener('submit', event => { event.preventDefault(); apply(); persist(); });
    window.addEventListener('popstate', restore);
    restore();
    form.hidden = false;
  }

  const chartSeries = new Map();
  const dashes = [[], [8, 4], [2, 4], [10, 3, 2, 3], [5, 3, 2, 3]];
  const numberFormat = new Intl.NumberFormat(document.documentElement.lang, {maximumFractionDigits: 1, notation: 'compact'});
  const day = 86400000;
  const isPoint = point => point && Number.isFinite(point.value) && Number.isFinite(Date.parse(point.week_start));

  function drawChart(canvas) {
    const series = chartSeries.get(canvas);
    if (!series) return;
    const points = series.flatMap(entry => entry.points);
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height || !points.length) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    const scale = Math.min(window.devicePixelRatio || 1, 3);
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    context.scale(scale, scale);
    const style = getComputedStyle(canvas);
    const color = key => style.getPropertyValue(key).trim();
    const left = 44, right = width - 16, top = 14, bottom = height - 30;
    const times = points.map(point => Date.parse(point.week_start));
    const first = Math.min(...times), last = Math.max(...times);
    let low = Math.min(...points.map(point => point.value));
    let high = Math.max(...points.map(point => point.value));
    if (low === high) { low -= 1; high += 1; }
    const x = time => first === last ? (left + right) / 2 : left + (time - first) / (last - first) * (right - left);
    const y = value => bottom - (value - low) / (high - low) * (bottom - top);

    context.font = '12px system-ui, sans-serif';
    context.textAlign = 'right';
    context.textBaseline = 'middle';
    for (const value of [low, (low + high) / 2, high]) {
      context.fillStyle = color('--muted');
      context.fillText(numberFormat.format(value), left - 7, y(value));
      context.strokeStyle = color('--rule');
      context.beginPath();
      context.moveTo(left, y(value));
      context.lineTo(right, y(value));
      context.stroke();
    }
    const iso = time => new Date(time).toISOString().slice(0, 10);
    context.textAlign = first === last ? 'center' : 'left';
    context.fillText(iso(first), x(first), height - 12);
    if (first !== last) {
      context.textAlign = 'right';
      context.fillText(iso(last), right, height - 12);
    }
    series.forEach((entry, index) => {
      context.strokeStyle = context.fillStyle = color('--series-' + index % dashes.length);
      context.lineWidth = 2.5;
      context.setLineDash(dashes[index % dashes.length]);
      context.beginPath();
      let previous = null;
      for (const point of entry.points) {
        const time = Date.parse(point.week_start);
        // A missing week is a gap in observations, including on static archives.
        if (previous === null || time - previous > 7 * day) context.moveTo(x(time), y(point.value));
        else context.lineTo(x(time), y(point.value));
        previous = time;
      }
      context.stroke();
      for (const point of entry.points) {
        context.beginPath();
        context.arc(x(Date.parse(point.week_start)), y(point.value), 3.8, 0, Math.PI * 2);
        context.fill();
      }
    });
  }

  const resizeCharts = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
    for (const {target} of entries) drawChart(target);
  }) : null;

  function showChart(canvas, entries) {
    if (!canvas) return;
    const series = entries.map(entry => ({points: (entry.points || []).filter(isPoint)
      .sort((a, b) => a.week_start.localeCompare(b.week_start))}));
    canvas.hidden = !series.some(entry => entry.points.length);
    chartSeries.set(canvas, series);
    if (!canvas.hidden) {
      resizeCharts?.observe(canvas);
      drawChart(canvas);
    }
  }

  function renderComparison(section, entries, label) {
    const usable = entries.filter(entry => entry.points.some(isPoint));
    section.querySelector('[data-compare-empty]').hidden = usable.length > 0;
    section.querySelector('[data-compare-results]').hidden = !usable.length;
    section.querySelector('#metric-heading').textContent = label;
    section.querySelector('[data-metric-label]').textContent = label;
    const key = section.querySelector('[data-compare-key]');
    const rows = section.querySelector('[data-compare-values]');
    key.replaceChildren();
    rows.replaceChildren();
    usable.forEach((entry, index) => {
      const series = document.createElement('li');
      const swatch = document.createElement('i');
      swatch.className = 'swatch series-' + index;
      swatch.setAttribute('aria-hidden', 'true');
      series.append(swatch, entry.full_name);
      key.append(series);
      for (const point of entry.points) {
        if (!isPoint(point)) continue;
        const row = rows.insertRow();
        const heading = document.createElement('th');
        heading.scope = 'row';
        heading.textContent = entry.full_name;
        row.append(heading);
        row.insertCell().textContent = point.week_start;
        row.insertCell().textContent = point.value;
      }
    });
    showChart(section.querySelector('#compare-chart'), usable);
  }

  function bindComparison(section) {
    const data = readJSON('comparison-data');
    const form = section.querySelector('form');
    const {repos: input, metric} = form.elements;
    const catalog = new Map(Object.entries(data.catalog));
    const names = [...catalog.keys()].sort();
    const canonical = new Map(names.map(name => [name.toLowerCase(), name]));
    const requestedNames = value => [...new Set(value.split(',').map(name => name.trim())
      .filter(Boolean).map(name => canonical.get(name.toLowerCase()) || name))];
    const metricLabel = () => metric.selectedOptions[0].textContent;

    if (!catalog.size) {
      // With no published catalog, keep the server-rendered GET form functional.
      renderComparison(section, data.initial, metricLabel());
      return;
    }

    function render(requested, announce) {
      const entries = requested.map(full_name => ({full_name, points: catalog.get(full_name)?.metrics[metric.value] || []}));
      renderComparison(section, entries, metricLabel());
      const missing = entries.filter(entry => !entry.points.some(isPoint)).map(entry => entry.full_name);
      const feedback = section.querySelector('[data-compare-feedback]');
      if (missing.length) feedback.textContent = data.missing + ' · ' + missing.join(', ');
      else feedback.textContent = announce ? data.shown + ' · ' + entries.length : '';
    }

    function suggest() {
      input.setCustomValidity('');
      const parts = input.value.split(',');
      parts.pop();
      const selected = requestedNames(parts.join(','));
      const prefix = selected.length ? selected.join(', ') + ', ' : '';
      const options = names.filter(name => !selected.includes(name)).map(name => new Option('', prefix + name));
      input.list.replaceChildren(...options);
    }

    function restore() {
      const params = new URLSearchParams(location.search);
      input.value = params.get('repos') ?? data.repos;
      const queryMetric = params.get('metric') ?? data.metric;
      metric.value = [...metric.options].some(opt => opt.value === queryMetric) ? queryMetric : 'hot_score';
      suggest();
      const requested = requestedNames(input.value);
      input.setCustomValidity(requested.length > 5 ? data.maxHint : '');
      render(requested.slice(0, 5), false);
    }

    input.addEventListener('input', suggest);
    form.addEventListener('submit', event => {
      event.preventDefault();
      const requested = requestedNames(input.value);
      if (requested.length > 5) {
        input.setCustomValidity(data.maxHint);
        input.reportValidity();
        return;
      }
      input.value = requested.join(', ');
      updateQuery({repos: requested.join(','), metric: metric.value});
      render(requested, true);
    });
    window.addEventListener('popstate', restore);
    restore();
  }

  document.querySelectorAll('[data-ranking]').forEach(bindRanking);
  document.querySelectorAll('[data-infra-filter]').forEach(bindInfra);
  document.querySelectorAll('[data-comparison]').forEach(bindComparison);
  const repoData = readJSON('repo-chart-data');
  if (repoData) {
    document.querySelectorAll('.trend-chart').forEach(canvas => showChart(canvas, [{points: repoData[canvas.dataset.series]}]));
  }
  if (chartSeries.size) {
    const redraw = () => chartSeries.forEach((_, canvas) => drawChart(canvas));
    if (!resizeCharts) window.addEventListener('resize', redraw);
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redraw);
  }
  syncLanguageLink();
  window.addEventListener('popstate', syncLanguageLink);
  window.addEventListener('hashchange', syncLanguageLink);
})();
