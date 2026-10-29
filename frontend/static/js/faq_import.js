/* Conferencia da importacao da FAQ (PN-T30): ao trocar a categoria de uma linha, tira o aviso
 * "confira" (borda ambar e legenda). Gravar funciona sem este script: a categoria vai nos
 * campos cat_<linha> do formulario.
 */
(function () {
  'use strict';

  var AMBER = ['border-amber-400', 'bg-amber-50'];

  document.addEventListener('change', function (e) {
    var select = e.target && e.target.closest ? e.target.closest('[data-faq-import-cat]') : null;
    if (!select) { return; }
    AMBER.forEach(function (cls) { select.classList.remove(cls); });
    select.classList.add('border-ink-300');
    var cell = select.parentNode;
    var hint = cell ? cell.querySelector('[data-faq-import-hint]') : null;
    if (hint && hint.parentNode) { hint.parentNode.removeChild(hint); }
  });
})();
