/* Chat do Inbox (PN-T22): mostra a conversa a partir da mensagem mais recente.
 *
 * O fragmento do chat (#chat-pane) inclui este script; o HTMX o executa a cada troca. Por isso
 * ha uma guarda para registrar o ouvinte uma unica vez, e cada execucao rola o que ja esta na tela.
 */
(function () {
  'use strict';

  /* retorno-humano.1 (RH-5): aberto pelo link do aviso, rola ate a pergunta do ticket e poe o
   * cursor na caixa de resposta; senao, mostra a mensagem mais recente. */
  function scrollToLatest() {
    document.querySelectorAll('[data-chat-scroll]').forEach(function (el) {
      var focus = el.querySelector('[data-focus]');
      if (focus) {
        el.scrollTop = Math.max(0, focus.offsetTop - el.offsetTop - 24);
      } else {
        el.scrollTop = el.scrollHeight;
      }
    });
    var box = document.querySelector('[data-reply-focus]');
    if (box) { box.focus({ preventScroll: true }); }
  }

  /* Celular (abaixo de 768 px, como o md: do Tailwind): com uma conversa aberta, a lista sai
   * da tela e o chat ocupa tudo; "Conversas" traz a lista de volta. So troca classes que ja
   * existem no CSS (flex/hidden). */
  var narrow = window.matchMedia ? window.matchMedia('(max-width: 767px)') : null;

  function showList(visible) {
    var list = document.querySelector('[data-inbox-list]');
    var pane = document.getElementById('chat-pane');
    if (!list || !pane) { return; }
    list.classList.toggle('hidden', !visible);
    list.classList.toggle('flex', visible);
    pane.classList.toggle('hidden', visible);
    pane.classList.toggle('flex', !visible);
  }

  function fitPhone() {
    if (narrow && narrow.matches && document.querySelector('#chat-pane [data-chat-scroll]')) {
      showList(false);
    }
  }

  if (!window.__inboxChatBound) {
    window.__inboxChatBound = true;
    document.addEventListener('htmx:afterSettle', function (e) {
      var target = e.detail && e.detail.target;
      if (target && target.id === 'chat-pane') { fitPhone(); scrollToLatest(); }
    });
    document.addEventListener('click', function (e) {
      var button = e.target && e.target.closest ? e.target.closest('[data-show-list]') : null;
      if (button) { showList(true); }
    });
    if (narrow && narrow.addEventListener) {
      narrow.addEventListener('change', function () {
        if (!narrow.matches) {
          var list = document.querySelector('[data-inbox-list]');
          var pane = document.getElementById('chat-pane');
          if (list) { list.classList.remove('hidden'); list.classList.add('flex'); }
          if (pane) { pane.classList.remove('hidden'); pane.classList.add('flex'); }
        }
      });
    }
  }
  fitPhone();
  scrollToLatest();
})();
