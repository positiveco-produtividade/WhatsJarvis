# Piloto técnico do Jarvis (descartável)

Prova de ponta a ponta: o celular do Caio manda mensagem para o número de teste
da Meta e o Jarvis responde com a FAQ (Guia de produtos + Nielsen + GMR), **sem IA**.
Spec: [docs/tasks/piloto-tecnico.1/spec/spec.md](../docs/tasks/piloto-tecnico.1/spec/spec.md).
A versão de produção será outra (Python/FastAPI no servidor).

## Rodar

1. Crie `piloto/.env` com o conteúdo de `piloto/configuracao-modelo.txt` e preencha (o `.env` não vai para o git).
2. Testes: `node piloto/teste.js`
3. Servidor: `node piloto/server.js` → ouve só em `http://127.0.0.1:8787/webhook`
4. Túnel (outro terminal): `cloudflared tunnel --url http://127.0.0.1:8787` → copie o endereço `https://….trycloudflare.com`
5. Na Meta (app Jarvis → WhatsApp → Configuração → Webhook):
   - URL de retorno: `https://….trycloudflare.com/webhook`
   - Verificar token: o mesmo `VERIFY_TOKEN` do `.env`
   - Assinar o campo **messages**
6. Mande "oi" do seu celular para o número de teste.

O endereço do túnel muda toda vez que o `cloudflared` é reiniciado: aí é preciso
atualizar a URL na Meta.

## Onde ver o que aconteceu

`piloto/logs/conversas.jsonl` (fora do git): uma linha por mensagem, com a
pergunta da FAQ encontrada, a nota de semelhança e o tempo. Telefones mascarados.
