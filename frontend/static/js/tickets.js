/* Tela Tickets (PN-T18): clicar na linha abre o Inbox na conversa do vendedor.
 * Delegado no document: a tabela e trocada pelo HTMX a cada filtro. Links, botoes e campos
 * dentro da linha mantem o comportamento proprio (o link do numero ja leva ao mesmo lugar). */
(function () {
  'use strict';

  document.addEventListener('click', function (e) {
    var row = e.target.closest('tr[data-href]');
    if (!row || e.target.closest('a, button, input, select, textarea')) { return; }
    var href = row.getAttribute('data-href');
    if (href && href.charAt(0) === '/' && href.charAt(1) !== '/') {
      window.location.assign(href);
    }
  });
})();
