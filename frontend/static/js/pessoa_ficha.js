/* Ficha da pessoa e Revogar (PN-T35): so devolve o foco ao primeiro campo do fragmento trocado.
 *
 * Os fragmentos (#modal-body) incluem este script; o ouvinte e registrado uma unica vez
 * (guarda em window). Tudo funciona sem ele: o formulario de revogar e um select comum.
 */
(function () {
  'use strict';

  if (window.__pessoaFichaBound) { return; }
  window.__pessoaFichaBound = true;

  document.addEventListener('htmx:afterSwap', function (e) {
    var target = e.detail && e.detail.target;
    if (!target || target.id !== 'modal-body') { return; }
    var field = target.querySelector('[data-ficha-focus]');
    if (field && field.focus) { field.focus(); }
  });
})();
