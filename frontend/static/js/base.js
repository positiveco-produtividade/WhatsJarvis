/* Componentes Alpine (build CSP) do painel Jarvis - PN-T07.
 *
 * Regras: so Alpine.data registrado aqui; nos templates, x-data e apenas o nome do
 * componente e as expressoes sao nomes de propriedade/metodo (nada de eval, objeto
 * literal ou handler inline). Carregado ANTES do Alpine para o 'alpine:init' pegar.
 *
 * Eventos usados (podem vir de HX-Trigger do servidor):
 *   toast          detail = {message, kind}  (kind: ok | erro | aviso)
 *   closeOverlays  fecha modal e drawer
 */
(function () {
  'use strict';

  var overlayStack = [];

  function closeTopOverlay() {
    var top = overlayStack[overlayStack.length - 1];
    if (top) { top.close(); }
  }

  function closeAllOverlays() {
    overlayStack.slice().forEach(function (o) { o.close(); });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeTopOverlay(); }
  });
  // De novo depois que o htmx termina a resposta (o afterSwap vem depois do HX-Trigger).
  document.addEventListener('closeOverlays', function () {
    closeAllOverlays();
    setTimeout(closeAllOverlays, 50);
  });

  function toast(message, kind) {
    document.dispatchEvent(new CustomEvent('toast', { detail: { message: message, kind: kind || 'ok' } }));
  }

  // Avisos rapidos em DOM puro (o Alpine CSP so avalia nomes simples, nao item.message).
  // Somem sozinhos em 6 s; o texto entra por textContent (nunca como HTML).
  var TOAST_CLASSES = {
    erro: 'bg-red-50 border-red-200 text-red-900',
    aviso: 'bg-amber-50 border-amber-200 text-amber-900',
    ok: 'bg-mint-50 border-mint-200 text-mint-900'
  };
  document.addEventListener('toast', function (e) {
    var d = (e.detail && typeof e.detail === 'object') ? e.detail : {};
    var message = d.message || d.value || '';
    var box = document.getElementById('toasts');
    if (!message || !box) { return; }
    var item = document.createElement('div');
    item.setAttribute('role', 'status');
    item.className = 'flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg ' +
      (TOAST_CLASSES[d.kind] || TOAST_CLASSES.ok);
    var text = document.createElement('span');
    text.textContent = String(message);
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'shrink-0 opacity-60 hover:opacity-100';
    close.setAttribute('aria-label', 'Fechar aviso');
    close.textContent = '✕';
    close.addEventListener('click', function () { item.remove(); });
    item.appendChild(text);
    item.appendChild(close);
    box.appendChild(item);
    setTimeout(function () { item.remove(); }, 6000);
  });

  // Erros de rede/servidor do HTMX viram aviso (sem janelas nativas).
  document.addEventListener('htmx:responseError', function () {
    toast('Não foi possível concluir a ação. Tente de novo.', 'erro');
  });
  document.addEventListener('htmx:sendError', function () {
    toast('Sem conexão com o servidor. Tente de novo.', 'erro');
  });

  // Modal e drawer: abrem quando o HTMX troca o conteudo do corpo; fecham no Esc, no fundo
  // e em qualquer [data-overlay-close].
  function overlay(bodyId) {
    return function () {
      return {
        open: false,
        lastFocus: null,
        init: function () {
          var self = this;
          document.addEventListener('htmx:afterSwap', function (e) {
            var target = e.detail && e.detail.target;
            if (!target || target.id !== bodyId) { return; }
            // Resposta de sucesso com HX-Reswap: none nao troca nada; o htmx ainda dispara
            // afterSwap no alvo e, sem esta guarda, reabria o modal recem-fechado.
            var xhr = e.detail.xhr;
            if (xhr && xhr.getResponseHeader('HX-Reswap') === 'none') { return; }
            if (!target.firstElementChild) { return; }
            self.show();
          });
          this.$el.addEventListener('click', function (e) {
            if (e.target.closest('[data-overlay-close]')) { self.close(); }
          });
        },
        show: function () {
          if (this.open) { return; }
          this.lastFocus = document.activeElement;
          this.open = true;
          overlayStack.push(this);
          var self = this;
          this.$nextTick(function () {
            if (self.$refs.panel) { self.$refs.panel.focus(); }
          });
        },
        close: function () {
          if (!this.open) { return; }
          this.open = false;
          overlayStack = overlayStack.filter(function (o) { return o !== this; }, this);
          var body = document.getElementById(bodyId);
          if (body) { body.innerHTML = ''; }
          if (this.lastFocus && this.lastFocus.focus) { this.lastFocus.focus(); }
          this.lastFocus = null;
        }
      };
    };
  }

  document.addEventListener('alpine:init', function () {
    Alpine.data('modal', overlay('modal-body'));
    Alpine.data('drawer', overlay('drawer-body'));

    // Menu lateral no celular.
    Alpine.data('mobileMenu', function () {
      return {
        open: false,
        toggle: function () { this.open = !this.open; },
        get expanded() { return this.open ? 'true' : 'false'; }
      };
    });

    // Selecao em lote (checkboxes [data-select-item] e um [data-select-all] opcional).
    Alpine.data('selection', function () {
      return {
        count: 0,
        init: function () { this.recount(); },
        items: function () { return this.$el.querySelectorAll('[data-select-item]'); },
        recount: function () {
          var n = 0;
          this.items().forEach(function (i) { if (i.checked) { n += 1; } });
          this.count = n;
        },
        onChange: function (e) {
          if (e.target.matches('[data-select-all]')) {
            var on = e.target.checked;
            this.items().forEach(function (i) { i.checked = on; });
          }
          this.recount();
        },
        get hasSelection() { return this.count > 0; },
        get label() { return this.count === 1 ? '1 selecionado' : this.count + ' selecionados'; }
      };
    });
  });
})();
