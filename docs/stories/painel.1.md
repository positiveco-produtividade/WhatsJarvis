# História painel.1 — Painel de gestão do Jarvis

> **Épico:** [painel](../epics/painel.md) · **Status:** Draft · **Owner:** Caio · **Criado:** 2026-10-22 · **Atualizado:** 2026-10-22 (protótipo aprovado e decisões do Caio)

## Como / quero / para

**Como** responsável pelo Jarvis,
**quero** um painel igual ao do Instagram (AUTO-1),
**para** ver as conversas, tratar os escalados, atualizar a FAQ, controlar quem pode usar, pausar o Jarvis, acompanhar o uso e ser avisado por e-mail de cada novo alerta, sem depender de comandos no servidor.

## Protótipo aprovado

`docs/designs/painel-prototipo.html` é a referência visual e de comportamento (agentes em `docs/designs/bots/`; coordenador em `docs/designs/bots/fichas/jarvis-coordenador.png`). Seletor de marca no topo do menu (Todas as marcas, Jungle, Plant Power, Possible, A Tal da Castanha, Zaya) escopa as telas; a conversa é por vendedor e a marca vem de cada ticket.

## Telas

| Grupo | Tela | Equivalente no AUTO-1 | O que mostra |
|---|---|---|---|
| Operação | **Tickets** | `logs`/tickets | 5 KPIs, busca, filtros de classificação, status e período; tabela Ticket/Data/Tipo/Status/Mensagem/Marca; linha abre o Inbox |
| Operação | **Inbox** | `inbox.html`, `_inbox_chat.html` | Conversas/Mídias, "Só aguardando", Concluir em lote; chat com horário em cada mensagem, separadores de dia, Bot/Humano e "respondeu em X"; "Responder em: Grupo da equipe (WhatsApp)" (desativado enquanto a chave do grupo estiver desligada); Tickets recentes |
| Operação | **Escalados** | `reclamacoes.html` | Quadro por classificação (Preço, Produto, Argumento, Material, Campanha, Sem classificação), urgência editável no cartão, botão Mídias, Mover para Concluída. Escalados (inclusive mídia) vivem só aqui: sem alerta e sem e-mail |
| Operação | **FAQ** | `admin/faq.html` (+ importar, variações) | Perguntas e respostas por marca e categoria (semente: Guia de produtos, 112); adicionar, editar, desativar/reativar; importar .xlsx/.csv com conferência antes de gravar; sem tags |
| Operação | **Pessoas autorizadas** | `customer.html` (adaptado) | Autorizadas, Pedindo acesso (tentativas de números desconhecidos, com a primeira mensagem do número, sujeito à validação jurídica da LGPD; não gera alerta nem e-mail) e Retiradas; busca única; cadastrar com todos os campos do PRD, importar CSV (grava as válidas), revogar (≤ 1 min), autorizar pedido, ignorar, reativar (novo aceite) |
| Análise | **Métricas** | `metrics.html` | Usuários ativos, tickets, resolvidos sem escalar, mensagens por dia, por marca, tempo de resposta, bloqueios |
| Análise | **Alertas** | `alerts.html` | Só problemas de sistema e segurança (falha de API, taxa de erro, pico de volume, injection, limite de 60 msg/h, pausa/retomada, marca ligada/desligada); Pausar/Retomar Jarvis e Jarvis por marca (sem modo conservador). Escalados e números desconhecidos não geram alerta |
| Análise | **Relatórios** | `relatorios.html` | Perguntas frequentes, objeções, perguntas sem resposta ("Criar na FAQ"), PDF do mês |
| Admin | **Logs** | `logs.html` | Tickets com problema e final do log do servidor, sem PII |
| Admin | **Perguntas** | `assistente_perguntas.html` | Tudo o que foi perguntado ao Assistente, com a resposta |
| Flutuante | **Assistente** | `_assistant_widget.html` | Jarvis (coordenador) e os agentes Duda, Léo, Nico, Rita, Tati; responde com dados do banco via Gemini |

## Regras

- Login pelo IAM corporativo, igual ao AUTO-1; só o Caio (admin único) por enquanto.
- Toda alteração (FAQ, pessoas, conclusão, reclassificação, urgência, controles do Jarvis, LGPD) fica registrada com autor e data.
- Cadastro pelo painel substitui o comando/CSV temporário da história porta-fechada.1.
- O painel não responde ao vendedor; concluir um ticket não envia mensagem.
- E-mail imediato ao Caio (padrão AUTO-1, Resend) somente para cada novo alerta; destinatário em configuração do servidor; falha de envio não bloqueia nada e é retentada. Escalado e número desconhecido não enviam e-mail.
- Grupo de WhatsApp da equipe (WAHA): caminho construído, desligado por chave de configuração (padrão). Desligado, nada é enviado a grupo; o e-mail de alertas não depende da chave.
- Dados pessoais (exportação e exclusão a pedido, retenção de 12 meses) tratados conforme LGPD.

## Depende de

porta-fechada.1 (tabelas de usuários, mensagens, tentativas, alertas e rastro), com emenda em andamento: guardar a primeira mensagem de desconhecido, permitir Reativar de revogado, campos completos do usuário e escalação/mídia/desconhecido sem alerta. Validação jurídica da LGPD para exibir a primeira mensagem de desconhecido.

## Critérios de aceite (alto nível)

1. Caio entra com a conta da empresa; quem não é admin não acessa.
2. Revogar uma pessoa no painel bloqueia o Jarvis para ela em ≤ 1 minuto, mesmo no meio de uma conversa.
3. FAQ editada no painel passa a ser usada pelo Jarvis em ≤ 5 minutos.
4. Inbox mostra as conversas de usuários autorizados, com busca por pessoa, horários e tempo de resposta.
5. Métricas e Relatórios mostram números do período e da marca escolhidos.
6. Pausar o Jarvis e desligar uma marca funcionam e ficam registrados; com o grupo da equipe desligado nada é enviado a grupo algum.
7. Cada alerta novo gera um e-mail imediato ao Caio; escalado aparece só em Escalados e número desconhecido só em Pedindo acesso, nunca em Alertas e sem e-mail.

Detalhe: `docs/tasks/painel.1/spec/` (64 critérios ativos, PN-1 a PN-67).
