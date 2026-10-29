# Aviso de privacidade — Jarvis (RASCUNHO)

> **Status:** rascunho para revisão jurídica — não publicar antes da aprovação
> **Preparado em:** 2026-10-19 · **Responsável:** Caio
> **Pontos para o jurídico decidir estão marcados com ⚠️**

---

## Parte 1 — Mensagem de boas-vindas no WhatsApp

Modelo a submeter à Meta. Fica curto porque o WhatsApp limita o tamanho e
exige aprovação de cada texto. O aviso completo (Parte 2) vai junto, em PDF ou
link ⚠️.

> Olá, {{nome}}! Eu sou o Jarvis, assistente de vendas da Positive Company. 👋
>
> Respondo suas dúvidas sobre produtos, preços e materiais das nossas marcas, aqui mesmo no WhatsApp.
>
> Para isso, guardo seu nome, telefone, região e nossas conversas, só para apoiar suas vendas. O aviso de privacidade completo está no documento anexo.
>
> Para começar, toque em **Aceito**.
>
> [ Aceito ] [ Não aceito ]

**Versão de teste submetida à Meta em 2026-10-27** (conta de teste, modelo
`jarvis_boas_vindas`, categoria Utilidade, pt_BR, variável posicional `{{1}}` =
nome, botões de resposta rápida "Aceito" e "Não aceito"). A Meta classificou o
texto acima como Marketing; este foi reescrito como ativação de cadastro para
caber em Utilidade. ⚠️ O jurídico revisa este texto antes do uso com vendedores:

> Olá, {{1}}. Seu acesso ao Jarvis, o assistente da equipe comercial da Positive Company, foi cadastrado.
>
> Para ativar o acesso, precisamos do seu aceite: guardamos seu nome, telefone, região e as conversas com o Jarvis, só para uso interno da equipe comercial.
>
> Toque em Aceito para ativar ou em Não aceito para recusar.
>
> [ Aceito ] [ Não aceito ]

⚠️ A Parte 2 (seção 3) ainda diz que áudios viram texto; hoje o Jarvis **não**
transcreve áudio: responde "equipe avisada" e abre um ticket. Ajustar o texto
quando o jurídico revisar.

Se a pessoa tocar em **Não aceito**:

> Tudo bem. Sem o aceite eu não consigo te atender por aqui. Se mudar de ideia, fale com seu gestor.

---

## Parte 2 — Aviso de privacidade completo

### 1. Quem somos

O Jarvis é um assistente automatizado da **Positive Company** ⚠️ *(razão
social e CNPJ)*, que é a controladora dos dados tratados por ele.

### 2. Para que serve

O Jarvis responde, pelo WhatsApp, às dúvidas do time comercial sobre os
produtos, preços, argumentos de venda e materiais das marcas da Positive
Company. Ele é usado **somente por pessoas cadastradas** pela empresa.

### 3. Quais dados coletamos

- **Cadastro:** nome, telefone, empresa, perfil (vendedor, gestor, parceiro), estados e canais em que atua, marcas, gestor responsável.
- **Uso:** as mensagens que você envia e recebe, os arquivos enviados a você, a data e a hora de cada conversa e as avaliações que você fizer das respostas.
- **Áudios:** quando você manda um áudio, ele é transformado em texto e **o áudio original é apagado** logo em seguida.

Não coletamos dados de clientes finais, e pedimos que você **não envie dados
pessoais de terceiros** (como nome ou CPF de compradores) nas conversas.

### 4. Por que tratamos esses dados

Uma única finalidade: **apoiar a venda do portfólio Positive Company**. Isso
inclui:
- responder suas perguntas com a informação certa para o seu perfil e região;
- controlar quem tem acesso e impedir que informações comerciais cheguem a quem não deveria;
- encaminhar a uma pessoa do time as perguntas que o Jarvis não souber responder;
- melhorar as respostas e entender quais dúvidas são mais comuns no campo.

**Base legal** ⚠️: *a definir pelo jurídico. Sugestões para avaliar:
legítimo interesse ou execução de contrato para colaboradores; execução de
contrato para distribuidores e representantes.*

### 5. Com quem compartilhamos

Os dados são processados por fornecedores de tecnologia contratados pela
Positive Company, apenas para fazer o Jarvis funcionar:

| Fornecedor | Para quê |
|---|---|
| Meta (WhatsApp) | Entregar as mensagens |
| Google Cloud | Hospedar o sistema e os dados (região São Paulo) e processar as perguntas com inteligência artificial (Gemini) |

Esses fornecedores **não usam suas conversas para treinar** seus próprios
modelos ⚠️ *(confirmar nos contratos)*. Não vendemos nem cedemos seus dados a
mais ninguém.

### 6. Por quanto tempo guardamos

| Dado | Prazo |
|---|---|
| Conversas e registro de acessos | 12 meses |
| Áudios | Apagados logo após virar texto |
| Cadastro | Enquanto o acesso estiver ativo ⚠️ *(e prazo após o desligamento)* |

### 7. Segurança

Acesso somente por números cadastrados, conexão criptografada, dados
criptografados no armazenamento, painel de gestão com login corporativo e
registro de quem alterou o quê. Materiais de uso interno são enviados com seu
nome e telefone como marca d'água.

### 8. Seus direitos

Você pode, a qualquer momento:
- saber quais dados temos sobre você;
- pedir correção de dados errados;
- pedir a exclusão dos seus dados;
- retirar o seu aceite (o acesso ao Jarvis é encerrado).

Para isso, fale com ⚠️ *(encarregado de dados / e-mail de contato)*.

### 9. Mensagens da Positive

O Jarvis só manda mensagem por iniciativa própria (por exemplo, para renovar
seu acesso) se você tiver dado o aceite. Envie **sair** para parar de receber
esses avisos; você continua podendo fazer perguntas.

### 10. Mudanças neste aviso

Se este aviso mudar, avisaremos pelo próprio Jarvis antes da mudança valer.

*Última atualização: ⚠️ (data da aprovação)*

---

## Checklist para o jurídico

- [ ] Razão social e CNPJ da controladora
- [ ] Base legal para cada perfil (colaborador, parceiro externo)
- [ ] Contato do encarregado de dados (DPO)
- [ ] Prazo de guarda do cadastro após desligamento
- [ ] Confirmar cláusulas de não uso para treino nos contratos com Meta e Google
- [ ] Formato do aviso completo junto à mensagem de boas-vindas (PDF anexo ou link)
- [ ] Aprovação final do texto
