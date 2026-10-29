/* Chat do Inbox (PN-T22): mostra a conversa a partir da mensagem mais recente.
 *
 * O fragmento do chat (#chat-pane) inclui este script; o HTMX o executa a cada troca. Por isso
 * ha uma guarda para registrar o ouvinte uma unica vez, e cada execucao rola o que ja esta na tela.
 */
(function () {
  'use strict';

  function scrollToLatest() {
    document.querySelectorAll('[data-chat-scroll]').forEach(function (el) {
      el.scrollTop = el.scrollHeight;
    });
  }

  if (!window.__inboxChatBound) {
    window.__inboxChatBound = true;
    document.addEventListener('htmx:afterSettle', function (e) {
      var target = e.detail && e.detail.target;
      if (target && target.id === 'chat-pane') { scrollToLatest(); }
    });
  }
  scrollToLatest();
})();
