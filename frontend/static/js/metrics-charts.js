/* Graficos da tela Metricas (PN-T47). Chart.js vem de /static/vendor (nunca CDN).
 * Os dados chegam em <script type="application/json" id="metrics-data"> (CSP: sem script
 * inline). Sem esse bloco ou sem Chart.js nao ha o que desenhar e a tela segue legivel
 * (os numeros tambem aparecem em texto). A area de resultado e trocada pelo HTMX a cada
 * periodo, entao os graficos sao recriados apos cada troca. */
(function () {
  'use strict';

  var INK = '#262626';
  var charts = {};

  function readData() {
    var node = document.getElementById('metrics-data');
    if (!node) { return null; }
    try {
      return JSON.parse(node.textContent || 'null');
    } catch (err) {
      return null;
    }
  }

  function destroy(id) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
  }

  function draw(id, config) {
    var canvas = document.getElementById(id);
    if (!canvas || !window.Chart) { return; }
    destroy(id);
    charts[id] = new window.Chart(canvas, config);
  }

  function series(data) {
    return { labels: data.labels || [], values: data.values || [] };
  }

  function render() {
    Object.keys(charts).forEach(destroy);
    var data = readData();
    if (!data) { return; }
    var days = series(data.days || {});
    var brands = series(data.brands || {});
    var base = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } };
    draw('chart-days', {
      type: 'bar',
      data: { labels: days.labels, datasets: [{ label: 'Mensagens', data: days.values, backgroundColor: INK, borderRadius: 3 }] },
      options: Object.assign({}, base, { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } })
    });
    draw('chart-brands', {
      type: 'bar',
      data: { labels: brands.labels, datasets: [{ label: 'Tickets', data: brands.values, backgroundColor: INK, borderRadius: 3 }] },
      options: Object.assign({}, base, { indexAxis: 'y', scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } })
    });
  }

  document.addEventListener('DOMContentLoaded', render);
  document.addEventListener('htmx:afterSettle', function (e) {
    var target = e.detail && e.detail.target;
    if (target && target.id === 'metrics-resultado') { render(); }
  });
})();
