# Revisão visual do painel (PN-59) — checklist por tela

Referência aprovada: `docs/designs/painel-prototipo.html`. Rode o painel
local, entre como administrador e confira cada item. Marque `[x]` ao aprovar;
anote divergências novas no fim.

Geral (todas as telas)
- [ ] Menu lateral com grupos Operação, Análise e Admin, e seletor de marca com o rosto de cada agente.
- [ ] Item ativo do menu em fundo escuro; cartões brancos com borda cinza e cantos arredondados.
- [ ] Banner de estado (IAM, pausa, avisos) aparece no topo quando aplicável.
- [ ] Mensagens de aviso (toast) somem sozinhas; modais fecham com Esc e no fundo; nenhuma janela do navegador (`confirm`/`alert`).
- [ ] Páginas 403, 404, 500 e "IAM indisponível" com texto em português simples.
- [ ] Estados vazios com o texto do protótipo, em todas as listas.

Telas
- [ ] **Tickets** — KPIs no topo, filtros, tabela paginada (25), kanban/cartões, gaveta lateral do ticket (conversa, notas, mudar status/responsável).
- [ ] **Inbox** — lista à esquerda (50), conversa à direita, abrir por `?u=`, concluir em lote.
- [ ] **Escalados** — colunas e textos do protótipo, ação de assumir/concluir.
- [ ] **FAQ** — lista (10 por página), busca, modal Nova pergunta, importar planilha com prévia e confirmação, modelo .xlsx para baixar.
- [ ] **Pessoas autorizadas** — lista com filtros, ficha, cadastro, revogar com confirmação em modal, pedidos de acesso, importar planilha (prévia), exportar/excluir (LGPD) exigindo digitar o nome.
- [ ] **Métricas** — KPIs e gráficos Mensagens por dia, Por marca, Tempo de resposta, Bloqueios; filtro de período e marca.
- [ ] **Alertas** — cartões por tipo, `?abrir=<id>` destaca o alerta; controles de pausa e de marcas ("Em quais marcas o Jarvis responde").
- [ ] **Relatórios** — Perguntas mais frequentes, Objeções mais citadas; exportar em PDF.
- [ ] **Logs e Erros** — abas Problemas, Servidor (final do log), Auditoria e Retenção.
- [ ] **Perguntas** (do assistente) — lista das perguntas feitas ao assistente.
- [ ] **Assistente (widget)** — botão flutuante; desligado mostra o aviso; com provedor ligado, envia e mostra resposta.

## Divergências conhecidas (aprovadas ou a decidir)

- **Menu "Dados" omitido:** o protótipo tem a tela Dados (subir base, conferir prévia); ela não existe nesta entrega, então o item não aparece no menu.
- **Fonte Inter:** o protótipo carregava do Google Fonts; o painel não usa CDN e cai para a fonte do sistema. Pode-se hospedar a Inter localmente, uma vez, com a permissão do Caio.
- **Datas** no formato `dd/mm/aaaa HH:MM` (fuso de São Paulo) em todo o painel.
- **Resumo das 08:05 no grupo:** o texto só aparece com a chave do grupo da equipe ligada (hoje desligada).
- **FAQ inicial:** base de 112 perguntas editável pela equipe; o item de creatina/Anvisa está sem resposta.
- **Gráficos:** Chart.js local (sem CDN); cores da paleta do protótipo.
- **Dados fictícios:** o protótipo usa dados de exemplo; o painel mostra dados reais e estados vazios quando não há nada.
- **Playwright não instalado:** os testes de ponta a ponta (T65/T66) não foram executados; esta revisão manual os substitui.

## Notas da revisão

(preencher pelo Caio)
