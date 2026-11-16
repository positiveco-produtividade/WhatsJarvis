/* Ficha da pessoa e Revogar (PN-T35): so devolve o foco ao primeiro campo do fragmento trocado.
 *
 * Os fragmentos (#modal-body) incluem este script; o ouvinte e registrado uma unica vez
 * (guarda em window). Tudo funciona sem ele: o formulario de revogar e um select comum.
 */
(function () {
  'use strict';

  if (window.__pessoaFichaBound) { return; }
  window.__pessoaFichaBound = true;

  // "Copiar link" do Jarvis (canal-waha.1, WA-11): copia e confirma no proprio botao.
  document.addEventListener('click', function (e) {
    var button = e.target && e.target.closest && e.target.closest('[data-copy-link]');
    if (!button) { return; }
    var link = button.getAttribute('data-copy-link') || '';
    var done = function () {
      var original = button.textContent;
      button.textContent = 'Copiado!';
      window.setTimeout(function () { button.textContent = original; }, 2000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(done, function () {});
    } else {
      var field = button.parentNode.querySelector('input');
      if (field) { field.select(); document.execCommand('copy'); done(); }
    }
  });

  document.addEventListener('htmx:afterSwap', function (e) {
    var target = e.detail && e.detail.target;
    if (!target || target.id !== 'modal-body') { return; }
    var field = target.querySelector('[data-ficha-focus]');
    if (field && field.focus) { field.focus(); }
  });
})();
