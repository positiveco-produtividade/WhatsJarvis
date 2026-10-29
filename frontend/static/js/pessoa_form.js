/* Formulario de cadastro de pessoa (PN-T34): "marcar todas" e "limpar" dos grupos de caixinhas
 * (UFs, canais, marcas). O fragmento (#modal-body) inclui este script a cada troca; o ouvinte e
 * registrado uma unica vez (guarda em window). Salvar funciona sem este script. */
(function () {
  'use strict';

  function setGroup(button, checked) {
    var fieldset = button.closest('fieldset');
    if (!fieldset) { return; }
    var boxes = fieldset.querySelectorAll('input[type="checkbox"]');
    for (var i = 0; i < boxes.length; i++) { boxes[i].checked = checked; }
  }

  if (!window.__pessoaFormBound) {
    window.__pessoaFormBound = true;
    document.addEventListener('click', function (e) {
      var target = e.target;
      if (!target || !target.closest) { return; }
      var all = target.closest('[data-pf-all]');
      if (all) { setGroup(all, true); return; }
      var none = target.closest('[data-pf-none]');
      if (none) { setGroup(none, false); }
    });
  }
})();
