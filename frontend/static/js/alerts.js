/* Alertas (PN-T42): destaca o cartao aberto por ?abrir=<id> (link do e-mail).
 * O servidor marca o cartao com data-alert-focus; aqui so rolamos ate ele e damos o foco,
 * uma vez, na carga da pagina. */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var card = document.querySelector('[data-alert-focus]');
    if (!card) { return; }
    card.scrollIntoView({ block: 'center' });
    card.focus({ preventScroll: true });
  });
})();
