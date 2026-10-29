/* Formulario da FAQ (PN-T27): adiciona e remove linhas de variacao da resposta.
 *
 * O fragmento do formulario (#modal-body) inclui este script; o HTMX o executa a cada troca.
 * Por isso o ouvinte de clique e registrado uma unica vez (guarda em window). Salvar funciona
 * sem este script: as linhas sao textareas comuns com o mesmo nome ("variations").
 */
(function () {
  'use strict';

  function addRow(form) {
    var template = form.querySelector('template[data-faq-var-template]');
    var list = form.querySelector('[data-faq-vars]');
    if (!template || !list) { return; }
    list.appendChild(template.content.cloneNode(true));
    var fields = list.querySelectorAll('textarea');
    if (fields.length) { fields[fields.length - 1].focus(); }
  }

  function removeRow(button) {
    var row = button.closest('[data-faq-var]');
    if (row && row.parentNode) { row.parentNode.removeChild(row); }
  }

  if (!window.__faqFormBound) {
    window.__faqFormBound = true;
    document.addEventListener('click', function (e) {
      var target = e.target;
      if (!target || !target.closest) { return; }
      var add = target.closest('[data-faq-var-add]');
      if (add) {
        var form = add.closest('[data-faq-form]');
        if (form) { addRow(form); }
        return;
      }
      var remove = target.closest('[data-faq-var-remove]');
      if (remove) { removeRow(remove); }
    });
    document.addEventListener('htmx:afterSettle', function (e) {
      var target = e.detail && e.detail.target;
      if (!target || target.id !== 'modal-body') { return; }
      var conflict = target.querySelector('[data-faq-conflict]');
      if (conflict && conflict.focus) { conflict.focus(); }
    });
  }
})();
