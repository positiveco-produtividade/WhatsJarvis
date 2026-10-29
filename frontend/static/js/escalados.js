/* Escalados (PN-T23): clicar no cartao abre a conversa do vendedor no Inbox.
 * Delegado no document: o quadro e trocado pelo HTMX a cada acao. Botoes, links e campos dentro
 * do cartao mantem o comportamento proprio. */
(function () {
  'use strict';

  document.addEventListener('click', function (e) {
    var card = e.target.closest('article[data-href]');
    if (!card || e.target.closest('a, button, input, select, textarea, label')) { return; }
    var href = card.getAttribute('data-href');
    if (href && href.charAt(0) === '/' && href.charAt(1) !== '/') {
      window.location.assign(href);
    }
  });
})();
