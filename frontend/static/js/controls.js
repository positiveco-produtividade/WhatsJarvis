/* Controles do Jarvis (PN-T43): ao abrir o modal de confirmacao de pausa ou de marca, o foco vai
 * para o botao de confirmar (teclado). Confirmar funciona sem este script: o formulario e comum
 * e envia o estado desejado. O ouvinte e registrado uma unica vez (o fragmento reexecuta o script). */
(function () {
  'use strict';

  if (window.__controlsBound) { return; }
  window.__controlsBound = true;

  document.addEventListener('htmx:afterSettle', function (e) {
    var target = e.detail && e.detail.target;
    if (!target || target.id !== 'modal-body') { return; }
    var button = target.querySelector('[data-control-form] [data-control-confirm]');
    if (button && button.focus) { button.focus(); }
  });
})();
