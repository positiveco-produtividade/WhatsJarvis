# Mapa AC -> teste (PAINEL-1, task PN-T67)

Lido por `test_ac_coverage.py`. Caminhos relativos a `backend/tests/`. Cada PN ativo (64: PN-1..PN-67 sem PN-46, PN-62, PN-64, removidos da spec) precisa de ao menos uma referencia automatizada que exista, ou estar na secao Manuais com motivo. Nao mapear PN removido como ativo.

## Automatizados

| PN | Testes |
|---|---|
| PN-1 | `panel/integration/test_access.py::test_sem_sessao_html_redireciona_ao_iam_com_next`; `panel/integration/test_access.py::test_next_nunca_aponta_para_host_de_fora`; `panel/integration/test_access.py::test_sem_sessao_json_da_401_e_htmx_pede_redirect`; `panel/integration/test_access.py::test_iam_valido_cria_sessao_e_cookie_opaco` |
| PN-2 | `panel/integration/test_access.py::test_nao_admin_recebe_403_sem_dados_e_sem_sessao`; `panel/integration/test_access.py::test_forbidden_auditado_no_maximo_uma_vez_por_minuto_por_email`; `panel/integration/test_access.py::test_admin_removido_da_lista_perde_o_acesso_da_sessao_viva`; `panel/integration/test_access.py::test_rota_comum_sem_route_class_tambem_recusa_com_status_certo` |
| PN-3 | `panel/integration/test_access.py::test_iam_fora_sem_sessao_mostra_pagina_503`; `panel/integration/test_access.py::test_iam_fora_nao_derruba_sessao_viva`; `panel/integration/test_access.py::test_iam_401_na_revalidacao_revoga_a_sessao`; `panel/integration/test_access.py::test_dev_auth_entra_sem_iam_fora_de_producao` |
| PN-4 | `panel/integration/test_access.py::test_post_sem_token_e_403`; `panel/integration/test_access.py::test_post_com_token_e_origin_passa_por_cabecalho`; `panel/integration/test_access.py::test_post_com_campo_csrf_de_formulario_passa`; `panel/integration/test_access.py::test_origin_ou_referer_de_fora_e_ausente_falham` |
| PN-5 | `panel/integration/test_access.py::test_marca_grava_cookie_e_volta_pela_referer_do_painel`; `panel/integration/test_access.py::test_marca_ignora_referer_de_fora`; `panel/integration/test_access.py::test_marca_invalida_vira_todas_e_cookie_invalido_tambem`; `panel/integration/test_access.py::test_marca_exige_admin` |
| PN-6 | `panel/integration/test_inbox_read.py`; `panel/integration/test_tickets_read.py`; `panel/unit/test_brand_scope.py` |
| PN-7 | `panel/integration/test_tickets_page.py`; `panel/integration/test_tickets_read.py` |
| PN-8 | `panel/integration/test_tickets_page.py`; `panel/integration/test_tickets_read.py` |
| PN-9 | `panel/integration/test_inbox_page.py`; `panel/integration/test_tickets_page.py` |
| PN-10 | `panel/integration/test_inbox_page.py::test_pagina_completa_com_abas_busca_e_lista`; `panel/integration/test_inbox_page.py::test_linha_mostra_classificacao_e_tempo_de_espera`; `panel/integration/test_inbox_page.py::test_telefone_nao_aparece_na_lista`; `panel/integration/test_inbox_page.py::test_linha_aponta_para_o_chat` |
| PN-11 | `panel/integration/test_inbox_chat.py::test_lote_conclui_so_os_aguardando_da_aba_e_pede_refresh`; `panel/integration/test_inbox_chat.py::test_lote_na_aba_midias_conclui_a_midia`; `panel/integration/test_inbox_chat.py::test_lote_respeita_a_marca_em_foco`; `panel/integration/test_inbox_chat.py::test_lote_sem_selecao_ou_sem_aguardando_avisa_sem_alterar` |
| PN-12 | `panel/integration/test_inbox_chat.py::test_chat_sem_caixa_de_resposta_e_grupo_desativado`; `panel/integration/test_inbox_chat.py::test_grupo_ativo_com_a_chave_ligada`; `panel/integration/test_inbox_chat.py::test_bolhas_com_horario_dia_bot_e_tempo_de_resposta`; `panel/integration/test_inbox_chat.py::test_selo_escalado_e_botao_so_com_ticket_aguardando` |
| PN-13 | `panel/integration/test_inbox_chat.py::test_chat_sem_caixa_de_resposta_e_grupo_desativado`; `panel/integration/test_inbox_chat.py::test_grupo_ativo_com_a_chave_ligada`; `panel/integration/test_inbox_chat.py::test_bolhas_com_horario_dia_bot_e_tempo_de_resposta`; `panel/integration/test_inbox_chat.py::test_selo_escalado_e_botao_so_com_ticket_aguardando` |
| PN-14 | `panel/integration/test_inbox_chat.py` |
| PN-15 | `panel/integration/test_ticket_drawer.py::test_sem_sessao_redireciona_ao_iam`; `panel/integration/test_ticket_drawer.py::test_drawer_aguardando_mostra_conteudo_sem_responder`; `panel/integration/test_ticket_drawer.py::test_drawer_por_status`; `panel/integration/test_ticket_drawer.py::test_erro_de_envio_mostra_codigo_da_meta` |
| PN-16 | `panel/integration/test_ticket_drawer.py::test_concluir_registra_autor_sem_enviar_nada`; `panel/integration/test_ticket_drawer.py::test_concluir_sem_autor_e_recusado`; `panel/integration/test_ticket_drawer.py::test_segunda_conclusao_mostra_ja_concluido_por`; `panel/integration/test_ticket_drawer.py::test_duas_conclusoes_simultaneas_so_uma_vence` |
| PN-17 | `panel/integration/test_ticket_drawer.py::test_concluir_registra_autor_sem_enviar_nada`; `panel/integration/test_ticket_drawer.py::test_concluir_sem_autor_e_recusado`; `panel/integration/test_ticket_drawer.py::test_segunda_conclusao_mostra_ja_concluido_por`; `panel/integration/test_ticket_drawer.py::test_duas_conclusoes_simultaneas_so_uma_vence` |
| PN-18 | `panel/integration/test_ticket_drawer.py::test_reclassificar_troca_audita_e_mantem_numero`; `panel/integration/test_ticket_drawer.py::test_reclassificar_recusa_atual_e_invalida`; `panel/integration/test_ticket_drawer.py::test_urgencia_troca_e_continua_escalado` |
| PN-19 | `panel/integration/test_escalados.py::test_sem_sessao_redireciona_ao_iam`; `panel/integration/test_escalados.py::test_quadro_mostra_so_escalados_aguardando_por_coluna`; `panel/integration/test_escalados.py::test_urgencia_ordena_o_cartao_dentro_da_coluna`; `panel/integration/test_escalados.py::test_marca_em_foco_filtra_o_quadro` |
| PN-20 | `panel/integration/test_escalados.py::test_botao_midias_conta_e_abre_o_quadro_de_midias`; `panel/integration/test_escalados.py::test_botao_midias_respeita_a_marca` |
| PN-21 | `panel/integration/test_escalados.py::test_mover_para_concluida_tira_do_quadro_e_nao_envia_nada`; `panel/integration/test_escalados.py::test_mover_sem_alerta_e_sem_email`; `panel/integration/test_escalados.py::test_destino_invalido_nao_altera`; `panel/integration/test_escalados.py::test_mover_ticket_inexistente_avisa` |
| PN-22 | `panel/integration/test_faq_page.py` |
| PN-23 | `panel/integration/test_faq_form.py::test_criar_grava_versao_auditoria_e_avisa`; `panel/integration/test_faq_form.py::test_criar_invalido_lista_todos_os_problemas_e_mantem_o_que_foi_digitado`; `panel/integration/test_faq_form.py::test_criar_sem_resposta_exige_resposta_se_o_bot_responde`; `panel/integration/test_faq_form.py::test_criar_duplicada_na_marca` |
| PN-24 | `panel/integration/test_faq_form.py::test_desativar_e_reativar_nao_apagam`; `panel/integration/test_faq_form.py::test_reativar_com_duplicata_avisa_erro`; `panel/integration/test_faq_form.py::test_lista_aponta_para_as_rotas_do_formulario` |
| PN-25 | `panel/integration/test_faq_reader.py::test_revisao_reflete_aos_59s_sem_esperar_o_teto`; `panel/integration/test_faq_reader.py::test_apos_301s_continua_refletindo`; `panel/integration/test_faq_reader.py::test_teto_de_idade_recarrega_sem_mudanca_de_revisao` |
| PN-26 | `panel/integration/test_faq_form.py::test_formulario_de_edicao_traz_o_item_e_a_versao`; `panel/integration/test_faq_form.py::test_salvar_incrementa_versao_e_audita`; `panel/integration/test_faq_form.py::test_conflito_mostra_texto_atual_e_nao_grava`; `panel/integration/test_faq_form.py::test_force_sobrescreve_apenas_com_force_1` |
| PN-27 | `panel/integration/test_faq_import_routes.py::test_enviar_cria_lote_sem_gravar_e_vai_a_conferencia`; `panel/integration/test_faq_import_routes.py::test_enviar_pelo_htmx_responde_hx_redirect`; `panel/integration/test_faq_import_routes.py::test_enviar_xlsx`; `panel/integration/test_faq_import_routes.py::test_arquivo_recusado_volta_o_formulario_com_mensagem_e_link` |
| PN-28 | `panel/integration/test_faq_import_routes.py::test_enviar_cria_lote_sem_gravar_e_vai_a_conferencia`; `panel/integration/test_faq_import_routes.py::test_enviar_pelo_htmx_responde_hx_redirect`; `panel/integration/test_faq_import_routes.py::test_enviar_xlsx`; `panel/integration/test_faq_import_routes.py::test_arquivo_recusado_volta_o_formulario_com_mensagem_e_link` |
| PN-29 | `panel/integration/test_faq_import_routes.py::test_gravar_com_categoria_escolhida_na_tela`; `panel/integration/test_faq_import_routes.py::test_gravar_duas_vezes_nao_duplica`; `panel/integration/test_faq_import_routes.py::test_categoria_invalida_nao_grava_e_volta_a_conferencia`; `panel/integration/test_faq_import_routes.py::test_gravar_lote_inexistente_ou_malformado` |
| PN-30 | `panel/integration/test_faq_seed.py` |
| PN-31 | `panel/integration/test_people_read.py`; `panel/integration/test_pessoas_page.py` |
| PN-32 | `panel/integration/test_pessoas_cadastro.py::test_cadastro_valido_grava_e_enfileira_boas_vindas`; `panel/integration/test_pessoas_cadastro.py::test_campo_ausente_nomeia_o_campo_e_nao_grava`; `panel/integration/test_pessoas_cadastro.py::test_perfil_fora_da_lista_e_varios_erros_voltam_juntos`; `panel/integration/test_pessoas_cadastro.py::test_valores_digitados_voltam_no_formulario` |
| PN-33 | `panel/integration/test_pessoas_cadastro.py::test_telefone_incompleto_e_recusado`; `panel/integration/test_pessoas_cadastro.py::test_telefone_duplicado_inclusive_pelo_nono_digito` |
| PN-34 | `panel/integration/test_pessoas_ficha.py::test_modal_revogar_lista_fixa_sem_outro`; `panel/integration/test_pessoas_ficha.py::test_revogar_grava_motivo_autor_e_audita_sem_enviar`; `panel/integration/test_pessoas_ficha.py::test_revogar_motivo_fora_da_lista_e_recusado`; `panel/integration/test_pessoas_ficha.py::test_revogar_proprio_admin_recusado_no_servidor` |
| PN-35 | `panel/integration/test_pessoas_ficha.py::test_modal_revogar_lista_fixa_sem_outro`; `panel/integration/test_pessoas_ficha.py::test_revogar_grava_motivo_autor_e_audita_sem_enviar`; `panel/integration/test_pessoas_ficha.py::test_revogar_motivo_fora_da_lista_e_recusado`; `panel/integration/test_pessoas_ficha.py::test_revogar_proprio_admin_recusado_no_servidor` |
| PN-36 | `panel/integration/test_pessoas_cadastro.py::test_pedido_preenche_o_telefone_sem_mostrar_a_primeira_mensagem`; `panel/integration/test_pessoas_cadastro.py::test_pedido_invalido_ou_inexistente_abre_formulario_em_branco`; `panel/integration/test_pessoas_cadastro.py::test_pedido_sem_telefone_abre_sem_prefill`; `panel/integration/test_pessoas_cadastro.py::test_autorizar_resolve_o_pedido_na_mesma_transacao` |
| PN-37 | `panel/integration/test_people_read.py::test_only_pending_requests_references_first_messages` |
| PN-38 | `panel/integration/test_pessoas_pedidos.py` |
| PN-39 | `panel/integration/test_pessoas_ficha.py::test_reativar_volta_a_pending_optin_com_aceite_novo`; `panel/integration/test_pessoas_ficha.py::test_reativar_pessoa_ativa_so_avisa`; `panel/integration/test_pessoas_ficha.py::test_reenviar_aviso_a_quem_ainda_nao_aceitou`; `panel/integration/test_pessoas_ficha.py::test_reativar_inexistente_404` |
| PN-40 | `panel/integration/test_people_import_service.py`; `panel/integration/test_pessoas_importar_routes.py`; `panel/unit/test_people_import_rules.py` |
| PN-41 | `panel/integration/test_metrics_page.py`; `panel/integration/test_metrics_read.py` |
| PN-42 | `panel/integration/test_relatorios_page.py`; `panel/integration/test_reports.py`; `panel/unit/test_report_pdf.py` |
| PN-43 | `panel/integration/test_alerts_page.py::test_sem_sessao_redireciona_ao_iam`; `panel/integration/test_alerts_page.py::test_vazio_mostra_estado_saudavel`; `panel/integration/test_inbox_page.py::test_lista_vazia_mostra_nada_aguardando_aqui`; `panel/integration/test_inbox_page.py::test_banco_sem_conversas_mostra_estado_vazio` |
| PN-44 | `panel/integration/test_alerts_page.py::test_rotulos_pt_br_de_todos_os_tipos`; `panel/integration/test_alerts_page.py::test_escalacao_midia_e_desconhecido_nao_sao_alertas`; `panel/integration/test_alerts_page.py::test_cartao_mostra_severidade_selo_detalhes_e_escapa_texto`; `panel/integration/test_alerts_page.py::test_ordem_mais_novo_primeiro_e_limite` |
| PN-45 | `panel/integration/test_alerts_controls.py` |
| PN-47 | `panel/integration/test_alerts_controls.py` |
| PN-48 | `panel/integration/test_alerts_controls.py` |
| PN-49 | `panel/integration/test_team_group_flag.py` |
| PN-50 | `panel/integration/test_assistant_api.py`; `panel/integration/test_assistant_widget.py`; `panel/unit/test_assistant_providers.py` |
| PN-51 | `panel/integration/test_assistant_widget.py` |
| PN-52 | `panel/integration/test_assistant_service.py`; `panel/security/test_assistant_no_write.py`; `panel/unit/test_assistant_providers.py` |
| PN-53 | `panel/integration/test_assistant_api.py`; `panel/integration/test_assistant_log.py`; `panel/integration/test_assistant_service.py` |
| PN-54 | `panel/integration/test_logs_page.py` |
| PN-55 | `panel/integration/test_alerts_controls.py`; `panel/integration/test_audit_tab.py`; `panel/integration/test_pessoas_ficha.py` |
| PN-56 | `panel/integration/test_pessoas_lgpd_routes.py::test_exportar_baixa_json_sem_cache_e_audita`; `panel/integration/test_pessoas_lgpd_routes.py::test_nenhum_get_exporta_ou_exclui`; `panel/integration/test_pessoas_lgpd_routes.py::test_exportar_sem_csrf_e_negado`; `panel/integration/test_pessoas_lgpd_routes.py::test_exportar_inexistente_404` |
| PN-57 | `panel/integration/test_pessoas_lgpd_routes.py::test_modal_excluir_pede_o_nome`; `panel/integration/test_pessoas_lgpd_routes.py::test_modal_excluir_inexistente_404`; `panel/integration/test_pessoas_lgpd_routes.py::test_excluir_com_nome_errado_nao_escreve_nada`; `panel/integration/test_pessoas_lgpd_routes.py::test_excluir_sem_o_campo_nao_escreve_nada` |
| PN-58 | `panel/integration/test_retention.py`; `panel/integration/test_retention_tab.py` |
| PN-59 | `panel/perf/test_perf.py::test_p95_ate_2s` |
| PN-60 | `panel/integration/test_escalados.py::test_frase_0805_so_com_a_chave_do_grupo_ligada`; `panel/integration/test_escalados.py::test_cartao_abre_o_drawer_e_leva_ao_inbox`; `panel/integration/test_team_group_flag.py::test_desligada_alerta_fica_so_no_painel_e_nada_vai_ao_waha`; `panel/integration/test_team_group_flag.py::test_desligada_aviso_de_midia_fica_so_no_painel` |
| PN-61 | `panel/integration/test_team_group_flag.py::test_ligada_alerta_vai_ao_grupo_sem_pii`; `panel/integration/test_team_group_flag.py::test_ligada_aviso_de_midia_vai_ao_grupo_sem_telefone`; `panel/integration/test_team_group_flag.py::test_ligada_falha_do_waha_nao_levanta_e_fica_para_retentar`; `panel/integration/test_team_group_flag.py::test_webhook_midia_com_chave_desligada_nada_ao_waha` |
| PN-63 | `panel/integration/test_escalados.py::test_urgencia_no_cartao_muda_e_continua_em_escalados`; `panel/integration/test_escalados.py::test_urgencia_invalida_e_recusada`; `panel/integration/test_escalados.py::test_urgencia_exige_csrf`; `panel/integration/test_escalados.py::test_acao_na_tela_de_midias_continua_nela` |
| PN-65 | `panel/integration/test_email_alerts.py::test_email_traz_link_do_painel_com_abrir_e_id_do_alerta` |
| PN-66 | `panel/integration/test_email_alerts.py::test_falha_do_resend_nao_levanta_retenta_e_registra`; `panel/integration/test_email_alerts.py::test_erro_de_rede_tambem_e_retentado_sem_propagar`; `panel/integration/test_email_alerts.py::test_desiste_no_limite_de_tentativas_e_registra_failed` |
| PN-67 | `panel/integration/test_no_alert_counts.py`; `panel/integration/test_people_read.py`; `panel/integration/test_pessoas_pedidos.py` |

## Manuais

- PN-59: revisao visual contra o prototipo, checklist em `docs/tasks/painel.1/visual-review.md` (a parte de desempenho e idioma e automatizada acima).
- PN-1: smoke do login real pelo IAM com o Caio em ambiente de producao (o fluxo e automatizado com IAM falso acima).

## Lacunas conhecidas

- PN-61: resumo das 08:05 no grupo nao implementado (grupo da equipe desligado por decisao do Caio); coberto por `panel/integration/test_team_group_flag.py::test_resumo_diario_das_0805_existe_como_laco_do_painel`, que e xfail strict ate o grupo ser ligado. Avisos de alerta e midia estao cobertos acima.
- E2E Playwright (tarefas T65 e T66) nao executados: Playwright nao esta instalado; a cobertura de interface vem dos testes de integracao.
