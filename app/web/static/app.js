(function () {
  function formatComponentName(name) {
    var components = (window.UI_TEXT && window.UI_TEXT.components) || {};
    return components[name] || name.replaceAll("_", " ");
  }

  function setDetail(item) {
    var name = document.getElementById("detail-name");
    if (!name || !item) return;
    document.getElementById("detail-name").textContent = item.repository.full_name;
    document.getElementById("detail-desc").textContent =
      item.repository.description || (window.UI_TEXT && window.UI_TEXT.noDescription) || "No description";
    document.getElementById("detail-score").textContent = item.hot_score;
    document.getElementById("detail-link").href = "repos/" + item.repository.owner + "/" + item.repository.name + "/";
    document.getElementById("github-link").href = item.repository.html_url;
    var container = document.getElementById("detail-components");
    container.innerHTML = "";
    Object.entries(item.score_breakdown.components || {}).forEach(function (entry) {
      var row = document.createElement("div");
      row.className = "component-row";
      row.innerHTML =
        "<span>" +
        formatComponentName(entry[0]) +
        "</span><strong>" +
        entry[1] +
        '</strong><div class="bar"><i style="width:' +
        entry[1] +
        '%"></i></div>';
      container.appendChild(row);
    });
  }

  function bindRankingTable() {
    document.querySelectorAll("[data-toggle-breakdown]").forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.stopPropagation();
        var row = button.closest("tr");
        var breakdown = row ? row.nextElementSibling : null;
        if (breakdown) breakdown.hidden = !breakdown.hidden;
      });
    });

    document.querySelectorAll(".ranking-row").forEach(function (row) {
      row.addEventListener("click", function () {
        var index = Number(row.dataset.index || 0);
        if (window.RANKING_ITEMS) setDetail(window.RANKING_ITEMS[index]);
        document.querySelectorAll(".ranking-row").forEach(function (candidate) {
          candidate.classList.remove("selected");
        });
        row.classList.add("selected");
      });
    });
  }

  function drawLineChart(canvas, points, color) {
    if (!canvas || !points || !points.length) return;
    var ctx = canvas.getContext("2d");
    var width = canvas.width;
    var height = canvas.height;
    var pad = 34;
    var values = points.map(function (point) {
      return Number(point.value || 0);
    });
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    if (min === max) {
      min = Math.max(0, min - 1);
      max = max + 1;
    }
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = "#d9ded8";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad, height - pad);
    ctx.lineTo(width - pad, height - pad);
    ctx.moveTo(pad, pad);
    ctx.lineTo(pad, height - pad);
    ctx.stroke();

    ctx.strokeStyle = color || "#107c72";
    ctx.lineWidth = 3;
    ctx.beginPath();
    points.forEach(function (point, index) {
      var x = pad + (index * (width - pad * 2)) / Math.max(points.length - 1, 1);
      var normalized = (Number(point.value || 0) - min) / (max - min);
      var y = height - pad - normalized * (height - pad * 2);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = color || "#107c72";
    points.forEach(function (point, index) {
      var x = pad + (index * (width - pad * 2)) / Math.max(points.length - 1, 1);
      var normalized = (Number(point.value || 0) - min) / (max - min);
      var y = height - pad - normalized * (height - pad * 2);
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    });

    ctx.fillStyle = "#667085";
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillText(String(Math.round(max)), 8, pad + 4);
    ctx.fillText(String(Math.round(min)), 8, height - pad + 4);
  }

  function drawRepoCharts() {
    if (!window.REPO_CHART_DATA) return;
    document.querySelectorAll(".trend-chart").forEach(function (canvas) {
      var series = canvas.dataset.series;
      drawLineChart(canvas, window.REPO_CHART_DATA[series], series === "hot_score" ? "#107c72" : "#b7791f");
    });
  }

  function drawCompareChart() {
    var canvas = document.getElementById("compare-chart");
    if (!canvas || !window.COMPARE_DATA || !window.COMPARE_DATA.length) return;
    var ctx = canvas.getContext("2d");
    var colors = ["#107c72", "#b7791f", "#2f5f9f", "#14804a", "#b42318"];
    var width = canvas.width;
    var height = canvas.height;
    var pad = 40;
    var allValues = [];
    window.COMPARE_DATA.forEach(function (item) {
      item.points.forEach(function (point) {
        allValues.push(Number(point.value || 0));
      });
    });
    if (!allValues.length) return;
    var min = Math.min.apply(null, allValues);
    var max = Math.max.apply(null, allValues);
    if (min === max) max = min + 1;
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = "#d9ded8";
    ctx.beginPath();
    ctx.moveTo(pad, height - pad);
    ctx.lineTo(width - pad, height - pad);
    ctx.moveTo(pad, pad);
    ctx.lineTo(pad, height - pad);
    ctx.stroke();
    window.COMPARE_DATA.forEach(function (item, seriesIndex) {
      var points = item.points || [];
      if (!points.length) return;
      ctx.strokeStyle = colors[seriesIndex % colors.length];
      ctx.lineWidth = 3;
      ctx.beginPath();
      points.forEach(function (point, index) {
        var x = pad + (index * (width - pad * 2)) / Math.max(points.length - 1, 1);
        var y =
          height -
          pad -
          ((Number(point.value || 0) - min) / (max - min)) * (height - pad * 2);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindRankingTable();
    drawRepoCharts();
    drawCompareChart();
  });
})();
