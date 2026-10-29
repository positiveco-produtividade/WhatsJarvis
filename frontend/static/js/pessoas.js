/* Tela Pessoas (PN-T33): clicar na linha abre a ficha da pessoa.
 * Delegado no document: a lista e trocada pelo HTMX a cada aba, busca e pagina. Botoes, links e
 * campos dentro da linha mantem o comportamento proprio; o clique na linha apenas aciona o
 * botao "Ver ficha" (que carrega a ficha no modal via HTMX). */
(function () {
  'use strict';

  document.addEventListener('click', function (e) {
    var row = e.target.closest('tr[data-ficha]');
    if (!row || e.target.closest('a, button, input, select, textarea')) { return; }
    var open = row.querySelector('[data-ficha-open]');
    if (open) { open.click(); }
  });
})();
