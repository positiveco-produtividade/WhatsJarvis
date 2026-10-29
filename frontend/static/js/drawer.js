/* Realce de acessibilidade do drawer do ticket (PN-T19).
 *
 * O drawer funciona so com HTMX (formularios com hx-post -> #drawer-body). Este script e
 * opcional: depois de cada troca do corpo do drawer, leva o foco ao aviso de resultado
 * ([data-drawer-status]) para o leitor de tela anunciar "Ja concluido por X em hh:mm" etc.
 * Sem Alpine e sem estado proprio. Paginas que incluem o drawer o carregam no bloco head:
 *   <script defer src="{{ static_v('js/drawer.js') }}"></script>
 */
(function () {
  'use strict';

  document.addEventListener('htmx:afterSwap', function (e) {
    var target = e.detail && e.detail.target;
    if (!target || target.id !== 'drawer-body') { return; }
    var status = target.querySelector('[data-drawer-status]');
    if (status && status.focus) { status.focus(); }
  });
})();
