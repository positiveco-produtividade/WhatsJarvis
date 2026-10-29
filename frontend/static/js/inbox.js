/* Inbox, lista de conversas (PN-T21). Componente Alpine CSP: selecao para o "Concluir" em lote.
 *
 * Os checkboxes ([data-select-item], mais um [data-select-all]) ficam no fragmento da lista,
 * que o HTMX troca ao filtrar/paginar; por isso a contagem e refeita apos cada troca.
 * Expressoes nos templates: so nomes de propriedade/metodo (build CSP).
 */
(function () {
  'use strict';

  document.addEventListener('alpine:init', function () {
    Alpine.data('inboxList', function () {
      return {
        count: 0,
        init: function () {
          var self = this;
          this.recount();
          document.addEventListener('htmx:afterSwap', function (e) {
            var target = e.detail && e.detail.target;
            if (target && target.id === 'inbox-lista') { self.recount(); }
          });
        },
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
          if (e.target.matches('[data-select-all], [data-select-item]')) { this.recount(); }
        },
        get noSelection() { return this.count === 0; },
        get selectedLabel() {
          if (this.count === 0) { return ''; }
          return this.count === 1 ? '1 selecionada' : this.count + ' selecionadas';
        }
      };
    });
  });
})();
