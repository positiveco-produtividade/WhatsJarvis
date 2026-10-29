/* Assistente flutuante do painel (PN-T57; PN-50, PN-51). Marcacao: templates/_assistant_widget.html.
 *
 * API usada: GET /api/assistant/status e POST /api/assistant/chat (JSON, cabecalho X-CSRF-Token).
 * Respostas do modelo viram HTML com marked e passam por DOMPurify antes de entrar na pagina;
 * se uma das duas bibliotecas faltar, a resposta entra como texto puro (nunca como HTML cru).
 * Texto da pessoa entra sempre via textContent.
 *
 * Este script carrega DEPOIS do Alpine (o include fica no fim do base.html), entao ele mesmo
 * registra o componente e inicia #assistente (Alpine.initTree), em vez de depender do alpine:init.
 */
(function () {
  'use strict';

  var STORE_KEY = 'jarvisbot.msgs';
  var MAX_HISTORY = 20;
  var DEFAULT_FACE = 'rosto-positive.png';
  var FALLBACK_NOTICE = 'O assistente será ativado quando a chave do Gemini for configurada';
  var COORDINATOR_SUB = 'Jarvis · coordenador do time · todas as marcas';
  var PANEL_BASE = 'fixed z-40 bg-white rounded-2xl shadow-2xl border border-ink-200 flex flex-col overflow-hidden transition-[width,height] duration-200 ';
  var PANEL_NORMAL = 'inset-x-2 bottom-2 top-16 sm:inset-auto sm:bottom-6 sm:right-6 sm:w-[440px] sm:h-[600px] sm:max-h-[calc(100vh-3rem)]';
  var PANEL_WIDE = 'inset-2 sm:inset-auto sm:bottom-6 sm:right-6 sm:w-[min(900px,calc(100vw-3rem))] sm:h-[calc(100vh-3rem)]';

  function csrfToken() {
    try {
      var headers = JSON.parse(document.body.getAttribute('hx-headers') || '{}');
      return headers['X-CSRF-Token'] || '';
    } catch (e) {
      return '';
    }
  }

  function loadStored() {
    try {
      var list = JSON.parse(sessionStorage.getItem(STORE_KEY) || '[]');
      return Array.isArray(list) ? list.filter(function (m) { return m && typeof m.text === 'string'; }) : [];
    } catch (e) {
      return [];
    }
  }

  function readJson(response) {
    return response.json().catch(function () { return {}; });
  }

  function answerHtml(text) {
    if (window.marked && window.DOMPurify) {
      return window.DOMPurify.sanitize(window.marked.parse(text, { async: false }));
    }
    return null;
  }

  function bubble(msg, faceUrl) {
    var row = document.createElement('div');
    var box = document.createElement('div');
    if (msg.role === 'user') {
      row.className = 'flex justify-end';
      box.className = 'bg-ink-900 text-white rounded-2xl rounded-br-sm max-w-[85%] px-3.5 py-2 text-sm whitespace-pre-wrap';
      box.textContent = msg.text;
    } else {
      row.className = 'flex justify-start items-end gap-2';
      var face = document.createElement('img');
      face.src = faceUrl;
      face.alt = '';
      face.width = 26;
      face.height = 26;
      face.setAttribute('aria-hidden', 'true');
      face.className = 'inline-block shrink-0 rounded-full ring-1 ring-ink-200';
      face.style.cssText = 'width:26px;height:26px;object-fit:cover;object-position:50% 20%;image-rendering:pixelated;background:#fff';
      if (msg.agent && msg.agent.label) { face.title = msg.agent.label; }
      row.appendChild(face);
      var tone = msg.error ? 'bg-red-50 border-red-200 text-red-900' : 'bg-white border-ink-200 text-ink-800';
      box.className = 'border rounded-2xl rounded-bl-sm max-w-[90%] px-3.5 py-2 text-sm leading-relaxed space-y-2 ' + tone;
      var html = msg.error ? null : answerHtml(msg.text);
      if (html === null) {
        box.className += ' whitespace-pre-wrap';
        box.textContent = msg.text;
      } else {
        box.innerHTML = html;
      }
    }
    row.appendChild(box);
    return row;
  }

  function assistente() {
    return {
      open: false,
      expanded: false,
      loading: false,
      statusLoaded: false,
      statusFailed: false,
      enabled: false,
      notice: '',
      draft: '',
      maxChars: 4000,
      agent: null,
      msgs: [],
      botsBase: '/static/img/bots/',
      disabledText: FALLBACK_NOTICE,

      init: function () {
        var el = this.$el;
        this.botsBase = el.getAttribute('data-bots-base') || this.botsBase;
        this.disabledText = el.getAttribute('data-disabled-text') || FALLBACK_NOTICE;
        this.msgs = loadStored();
        var last = this.lastAgent();
        this.agent = last;
        var self = this;
        this.$nextTick(function () { self.renderList(); });
      },

      // ---- estado derivado (o build CSP so aceita nomes de propriedade/metodo) ----
      get closed() { return !this.open; },
      get isEmpty() { return this.msgs.length === 0; },
      get hasMessages() { return this.msgs.length > 0; },
      get isDisabled() { return this.statusLoaded && !this.enabled; },
      get notDisabled() { return !this.isDisabled; },
      get chipsOff() { return !this.enabled || this.loading; },
      get inputOff() { return !this.enabled || this.loading; },
      get sendOff() { return !this.enabled || this.loading || this.draft.trim() === ''; },
      get panelClass() { return PANEL_BASE + (this.expanded ? PANEL_WIDE : PANEL_NORMAL); },
      get headFace() { return this.faceUrl(this.agent); },
      get headTitle() { return this.agent && this.agent.label ? this.agent.label : 'Jarvis'; },
      get headSub() {
        if (this.agent && this.agent.brand) {
          var parts = String(this.agent.label || '').split(' · ');
          return 'Jarvis · atendimento ' + (parts[1] || parts[0]);
        }
        return COORDINATOR_SUB;
      },

      faceUrl: function (agent) {
        return this.botsBase + (agent && agent.face_image ? agent.face_image : DEFAULT_FACE);
      },

      lastAgent: function () {
        for (var i = this.msgs.length - 1; i >= 0; i -= 1) {
          var m = this.msgs[i];
          if (m.role === 'assistant' && !m.error && m.agent) { return m.agent; }
        }
        return null;
      },

      // ---- abrir/fechar ----
      toggle: function () { if (this.open) { this.close(); } else { this.show(); } },
      show: function () {
        this.open = true;
        var self = this;
        if (!this.statusLoaded || this.statusFailed) { this.loadStatus(); }
        this.$nextTick(function () {
          if (self.$refs.input && !self.$refs.input.disabled) { self.$refs.input.focus(); }
          self.scrollDown();
        });
      },
      close: function () { this.open = false; },
      toggleExpand: function () { this.expanded = !this.expanded; },
      reset: function () {
        this.msgs = [];
        this.agent = null;
        this.persist();
        this.renderList();
      },

      // ---- status (PN-50) ----
      loadStatus: function () {
        var self = this;
        return fetch('/api/assistant/status', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
          .then(function (r) { return readJson(r).then(function (body) { return { ok: r.ok, body: body }; }); })
          .then(function (res) {
            if (!res.ok) { throw new Error('status'); }
            self.enabled = res.body.enabled === true;
            if (typeof res.body.max_chars === 'number') { self.maxChars = res.body.max_chars; }
            self.notice = self.enabled ? '' : (res.body.reason || self.disabledText);
            self.statusFailed = false;
            self.statusLoaded = true;
          })
          .catch(function () {
            self.enabled = false;
            self.notice = 'Não foi possível verificar o assistente agora. Feche e abra de novo.';
            self.statusLoaded = true;
            self.statusFailed = true;
          });
      },

      // ---- envio ----
      chip: function (e) { this.send(e.currentTarget.getAttribute('data-question') || ''); },
      submit: function () { this.send(this.draft); },

      history: function () {
        return this.msgs
          .filter(function (m) { return !m.error; })
          .slice(-MAX_HISTORY)
          .map(function (m) { return { role: m.role, text: m.text }; });
      },

      send: function (raw) {
        var text = String(raw || '').trim();
        if (!text || !this.enabled || this.loading || text.length > this.maxChars) { return; }
        var history = this.history();
        this.msgs.push({ role: 'user', text: text });
        this.draft = '';
        this.loading = true;
        this.renderList();
        var self = this;
        return fetch('/api/assistant/chat', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRF-Token': csrfToken() },
          body: JSON.stringify({ message: text, history: history })
        })
          .then(function (r) { return readJson(r).then(function (body) { return { status: r.status, ok: r.ok, body: body }; }); })
          .then(function (res) { self.onReply(res); })
          .catch(function () { self.pushError('Sem conexão com o servidor. Tente de novo.'); })
          .then(function () { self.finishSend(); });
      },

      onReply: function (res) {
        if (res.ok && typeof res.body.text === 'string') {
          this.agent = res.body.agent || null;
          this.msgs.push({ role: 'assistant', text: res.body.text, agent: res.body.agent || null });
          return;
        }
        if (res.status === 503) {
          this.enabled = false;
          this.notice = res.body.text || this.disabledText;
        }
        this.pushError(res.body.text || 'Não consegui responder agora. Tente de novo em instantes.');
      },

      pushError: function (message) {
        this.msgs.push({ role: 'assistant', text: message, error: true, agent: this.agent });
      },

      finishSend: function () {
        this.loading = false;
        this.persist();
        this.renderList();
        var self = this;
        this.$nextTick(function () {
          if (self.$refs.input && !self.$refs.input.disabled) { self.$refs.input.focus(); }
        });
      },

      // ---- persistencia na aba e desenho da lista ----
      persist: function () {
        try {
          sessionStorage.setItem(STORE_KEY, JSON.stringify(this.msgs.map(function (m) {
            return { role: m.role, text: m.text, error: m.error === true, agent: m.agent || null };
          })));
        } catch (e) { /* sem armazenamento: a conversa vale so nesta tela */ }
      },

      renderList: function () {
        var list = this.$refs.list;
        if (!list) { return; }
        while (list.firstChild) { list.removeChild(list.firstChild); }
        var self = this;
        this.msgs.forEach(function (m) { list.appendChild(bubble(m, self.faceUrl(m.agent))); });
        this.scrollDown();
      },

      scrollDown: function () {
        var box = this.$refs.scroller;
        if (box) { box.scrollTop = box.scrollHeight; }
      }
    };
  }

  function start(alpineRunning) {
    var root = document.getElementById('assistente');
    if (!root) { return; }
    Alpine.mutateDom(function () { root.setAttribute('x-data', 'assistente'); });
    if (alpineRunning) { Alpine.initTree(root); }
  }

  if (window.Alpine) {
    Alpine.data('assistente', assistente);
    start(true);
  } else {
    document.addEventListener('alpine:init', function () {
      Alpine.data('assistente', assistente);
      start(false);
    });
  }
})();
