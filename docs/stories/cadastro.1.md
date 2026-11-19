# História cadastro.1 — Editar o cadastro e trocar o número pelo painel

> **Épico:** [painel](../epics/painel.md) · **Status:** Implementada em 2026-11-19 (fases 1 a 3 aprovadas por Caio em 2026-11-18; suíte inteira, perf e eval palavras verdes) · **Owner:** Caio · **Criado:** 2026-11-18

Fonte de verdade: `docs/tasks/cadastro.1/spec/spec.md` e `requirements.json`.
Em caso de divergência, vale o spec.

## Como / quero / para

**Como** admin do painel,
**quero** editar os dados de uma pessoa cadastrada e trocar o número dela pela ficha,
**para** liberar marcas, corrigir UFs e perfis sem mexer no banco nem na linha de comando.

## Escopo (resumo)

- "Editar" na ficha: nome, perfil, empresa, UFs, canais, marcas, gestor e validade, com as regras do cadastro.
- Marcas valem na próxima mensagem; marca da conversa retirada é apagada.
- Liberar marca que a pessoa pediu conclui o pedido e avisa o vendedor.
- "Trocar número" na ficha, com a regra do `swap-number` (novo aceite).

Origem: lacuna registrada em `docs/STATE.md` (B-001, 2026-10-27).
