"""Painel de gestao do Jarvis (spec painel.1, 4.1).

Pacote isolado: o webhook e os servicos de PF nunca o importam (teste de arquitetura).
So `app.main` o monta, e apenas com `PANEL_ENABLED=true`.
"""
