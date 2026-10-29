/* Conferencia da importacao de pessoas (PN-T38): ao gravar, desliga o botao para evitar envio
 * duplo. Gravar funciona sem este script (o servico tambem nao grava duas vezes).
 */
(function () {
  'use strict';

  document.addEventListener('submit', function (e) {
    var form = e.target && e.target.closest ? e.target.closest('[data-pessoas-import-review]') : null;
    if (!form) { return; }
    var buttons = form.querySelectorAll('button[type="submit"]');
    // Adia o desligamento: desabilitar durante o evento cancelaria o envio em alguns navegadores.
    window.setTimeout(function () {
      Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    }, 0);
  });
})();
