"""Guardas sem custo e classificacao por palavras (spec 4.4 est. 1 e 4.7), sem IA, sem I/O.

Funcoes puras sobre o texto do vendedor, em PT-BR, sem acento e sem caixa. Decidem
antes de qualquer consulta ao Gemini: saudacao (CB-9), injection (CB-12), tema
interno (CB-19), condicao comercial e calculo (CB-8/CB-19). Tambem detectam
intencao de preco, classificam por palavras-chave e dao a urgencia da tabela 4.7.

Os padroes sao deliberadamente especificos: um falso positivo bloqueia uma
pergunta legitima do vendedor (o corpus de `test_intents.py` mede isso).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.values import BrainOutcome
from app.services.brain.matching import normalize

# Valores do CHECK de chat.tickets.classification / urgency (nao importamos app.panel).
PRECO = "PRECO"
NAO_IDENTIFICADO = "NAO_IDENTIFICADO"
URGENTE = "urgente"
POUCO_URGENTE = "pouco_urgente"

REASON_INJECTION = "injection"
REASON_OUT_OF_SCOPE = "out_of_scope"
REASON_COMMERCIAL = "commercial_condition"
REASON_CALC = "calc_not_supported"


@dataclass(frozen=True)
class GuardResult:
    """Desfecho decidido sem custo: o provider so executa os efeitos."""

    outcome: BrainOutcome
    escalation_reason: str | None
    classification: str
    thanks: bool = False  # saudacao que e so agradecimento (texto proprio, mesmo desfecho)


def _squash(text: str) -> str:
    """Normaliza e colapsa espacos."""
    return " ".join(normalize(text).split())


def _squeeze(word: str) -> str:
    """Letras repetidas viram uma ("oiii" -> "oi"), para tolerar alongamentos."""
    return re.sub(r"(.)\1+", r"\1", word)


def _compile(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


# --------------------------------------------------------------------------- saudacao

_GREETING_WORDS = (
    "oi ola opa eai e ai hey hello hi bom boa dia tarde noite tudo bem certo beleza blz "
    "tranquilo obrigado obrigada brigado brigada valeu vlw show ok okay joia top legal "
    "otimo perfeito entendi certo combinado fechado agradeco agradecido muito grato grata "
    "demais por favor com voce vc voces ai pessoal time equipe jarvis a de to td tb"
).split()
_GREETING_VOCAB: frozenset[str] = frozenset(_squeeze(w) for w in _GREETING_WORDS)


def is_greeting(text: str) -> bool:
    """So cumprimento/agradecimento, vazio ou so emoji (CB-9)."""
    words = _squash(text).split()
    if not words:
        return True
    return all(_squeeze(w) in _GREETING_VOCAB for w in words)


_THANKS_WORDS = (
    "obrigado obrigada brigado brigada valeu vlw agradeco agradecido grato grata".split()
)
_THANKS_VOCAB: frozenset[str] = frozenset(_squeeze(w) for w in _THANKS_WORDS)
_OPENERS: frozenset[str] = frozenset(
    _squeeze(w) for w in "oi ola opa eai hey hello hi bom boa dia tarde noite".split()
)


def is_thanks(text: str) -> bool:
    """Agradecimento (sem abertura de conversa): responde "por nada", nao "oi" (CB-9)."""
    words = {_squeeze(w) for w in _squash(text).split()}
    return is_greeting(text) and bool(words & _THANKS_VOCAB) and not words & _OPENERS


# --------------------------------------------------------------------------- injection

_INJECTION = _compile(
    (
        r"\b(?:ignor\w*|desconsider\w*|esquec\w*|descart\w*|desobede\w*)\b.{0,30}"
        r"\b(?:instrucoes|instrucao|prompt|diretrizes|orientacoes)\b",
        r"\b(?:ignor\w*|desconsider\w*|esquec\w*|descart\w*)\b.{0,20}"
        r"\b(?:suas|tuas|todas as|as) regras\b",
        r"\b(?:ignor\w*|desconsider\w*|esquec\w*)\b.{0,25}\b(?:regras|tudo|mensagens?)\b"
        r".{0,15}\b(?:anteriores?|acima|de antes|de seguranca|do sistema)\b",
        r"\bvoce agora e\b",
        r"\bvoce nao e mais\b",
        r"\ba partir de agora,? (?:voce|vc) (?:e|sera|vai ser|deve|nao precisa|pode)\b",
        r"\b(?:finja|fingir|simule)\b.{0,15}\b(?:que (?:voce )?(?:e|ser)|ser)\b",
        r"\bfaca de conta que voce\b",
        r"\b(?:assuma|assumir) o papel\b",
        r"\b(?:aja|atue|responda|comporte se) como (?:se fosse )?(?:um|uma|o|a)? ?"
        r"(?:ia |bot |assistente )?"
        r"(?:sem|livre|outro|outra|administrador|admin|desenvolvedor|hacker)",
        r"\b(?:mostr\w*|revel\w*|exib\w*|repit\w*|imprim\w*|vaz\w*|copi\w*|traduz\w*|"
        r"diga|passe|envie|me de)\b.{0,25}\b(?:seu|teu|o|do|de) (?:system |sistema )?prompt\b",
        r"\bqual (?:e|eh) (?:o )?(?:seu|teu) prompt\b",
        r"\bsystem prompt\b|\bprompt (?:do|de) sistema\b|\bprompt inicial\b",
        r"\b(?:suas|tuas) instrucoes (?:internas|iniciais|originais|secretas|de sistema)\b",
        r"\binstrucoes (?:internas|iniciais|originais|secretas|do sistema)\b",
        r"\b(?:repit\w*|mostr\w*|imprim\w*) (?:tudo|o texto|as palavras) (?:acima|anterior)\b",
        r"\bmodo (?:desenvolvedor|dev|deus|admin|administrador|debug|jailbreak|sem filtro)\b",
        r"\bdeveloper mode\b|\bjailbreak\b|\bdan mode\b",
        r"\b(?:desativ\w*|desabilit\w*|remov\w*|burl\w*|contorn\w*) "
        r"(?:seus|suas|os|as|todos os|todas as) "
        r"(?:filtros?|restricoes|regras|protecoes|limites|bloqueios)\b",
        r"\bignore (?:all |any )?(?:the )?(?:previous|prior|above) "
        r"(?:instructions|rules|prompts?)\b",
        r"\bdisregard (?:all |any )?(?:the )?(?:previous|prior|above)\b",
        r"\byou are now\b|\bact as (?:a|an)\b|\breveal (?:your|the) (?:system )?prompt\b",
        r"\bsou (?:o |a )?(?:administrador|desenvolvedor|programador|dono) "
        r"(?:do|da) (?:sistema|jarvis|bot|agente)\b",
        r"\b(?:dados?|telefones?|numeros?|conversas?|historico|mensagens|perguntas|cadastros?|"
        r"contatos?|emails?) (?:de|dos|das) (?:outros|outras|todos os|todas as|demais) "
        r"(?:usuarios|vendedores|vendedoras|pessoas|representantes)\b",
        r"\b(?:lista|listar|liste|todos os) (?:de |dos )?(?:todos os )?(?:usuarios|vendedores)"
        r" (?:cadastrados|do sistema|do jarvis|ativos)\b",
        r"\bo que (?:os )?(?:outros|demais) (?:vendedores|usuarios) (?:perguntaram|perguntam|"
        r"falaram|mandaram)\b",
    )
)
_INJECTION_RAW = re.compile(
    r"</?\s*(?:system|assistant|instructions?)\s*>|\[\s*(?:system|inst)\s*\]|^\s*#{2,}\s*system\b",
    re.IGNORECASE | re.MULTILINE,
)


def is_injection(text: str) -> bool:
    """Tentativa de mudar regras/perfil/permissoes ou de ler prompt/outros usuarios (CB-12)."""
    return bool(_INJECTION.search(_squash(text)) or _INJECTION_RAW.search(str(text)))


# --------------------------------------------------------------------------- tema interno

_INTERNAL = _compile(
    (
        r"\bmargem\b.{0,20}\b(?:da|do|de) (?:positive|empresa|voces|fabricante|industria|"
        r"fornecedor|distribuidora)\b",
        r"\b(?:positive|empresa|voces) (?:ganha|ganham|lucra|lucram|fatura|faturam)\b",
        r"\bquanto (?:a )?(?:positive|empresa|voces) "
        r"(?:ganha|ganham|lucra|lucram|fatura|faturam|paga|pagam)\b",
        r"\b(?:lucro|faturamento|receita|resultado financeiro) (?:da|do) (?:positive|empresa)\b",
        r"\bcustos? (?:de |da |do |dos )?(?:producao|fabricacao|industrializacao|materia prima|"
        r"ingredientes|empresa|positive|operacao)\b",
        r"\bquanto custa (?:pra|para) (?:voces )?(?:produzir|fabricar|fazer)\b",
        r"\bmetas? (?:da|do|de|das|dos) (?:positive|empresa|time|equipe|diretoria|comercial|"
        r"vendas|vendedores?|gerentes?|supervisores?|regional|trimestre|semestre|ano)\b",
        r"\bsalarios?\b",
        r"\bquanto (?:o |a )?(?:gerente|diretor|diretora|ceo|dono|dona|"
        r"supervisor\w*|coordenador\w*|vendedor\w*) (?:ganha|recebe)\b",
        r"\b(?:telefone|celular|email|whatsapp|contato|endereco) (?:do|da|de) "
        r"(?:gerente|diretor|diretora|ceo|dono|dona|supervisor\w*|coordenador\w*)\b",
        r"\bquem e (?:o |a )?(?:ceo|dono|dona|diretor|diretora) (?:da )?(?:positive|empresa)?\b",
        r"\b(?:demissao|demitir|demitido|promocao do|avaliacao de desempenho)\b",
    )
)


def is_internal_topic(text: str) -> bool:
    """Custos, margem da Positive, metas, pessoas: recusa de uma frase (CB-19)."""
    return bool(_INTERNAL.search(_squash(text)))


# --------------------------------------------------------------------------- comercial / calculo

_COMMERCIAL = _compile(
    (
        r"\bdescontos?\b|\bdescontao\b",
        r"\bbonific\w*",
        r"\bprazos? (?:de |do |para |pra )?(?:pagamento|pagar|faturamento|entrega|boleto)\b",
        r"\bcondico?es? (?:comerciais?|de pagamento|especiais?|de compra)\b",
        r"\bcondicao (?:comercial|de pagamento|especial)\b",
        r"\bnegoci(?:ar|acao) (?:de )?(?:preco|valor|prazo|condicao)\b",
        r"\bpreco especial\b|\bpreco promocional para\b",
    )
)
_CALC = _compile(
    (
        r"\bcalcul\w*",
        r"\bmarkup\b",
        r"\bmargem\b",
        r"\bquanto (?:eu )?(?:vou )?(?:ganho|lucro|sobra|ganharia|lucraria)\b",
        r"\bpreco (?:de )?(?:venda|gondola) (?:ideal|sugerido) (?:com|para|pra) (?:margem|lucro)\b",
    )
)


def is_commercial_condition(text: str) -> bool:
    """Desconto, prazo de pagamento, bonificacao: vai ao Comercial (CB-19)."""
    return bool(_COMMERCIAL.search(_squash(text)))


def is_calc_intent(text: str) -> bool:
    """Calculo de margem/preco ao consumidor (fora de escopo, RF-12)."""
    return bool(_CALC.search(_squash(text)))


# --------------------------------------------------------------------------- preco

_PRICE = _compile(
    (
        r"\bprecos?\b",
        r"\bcust(?:a|am|ar|aria)\b",
        r"\bvalor(?:es)?\b(?! (?:nutricion\w*|energetic\w*|diario|calorico|biologico))",
        r"\bquanto (?:esta|ta|sai|fica|vale|e|eh|custa|cobram|cobra|pago|paga)\b",
        r"\bpor quanto\b",
        r"\br\s?\d",
        r"\btabela de precos?\b",
    )
)


def has_price_intent(text: str) -> bool:
    """A mensagem pergunta preco (CB-8): o valor so pode vir da fonte."""
    norm = _squash(text)
    return bool(_PRICE.search(norm) or re.search(r"r\$", str(text).lower()))


# --------------------------------------------------------------------------- classificacao

_KEYWORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PRODUTO",
        _compile(
            (
                r"\bingredientes?\b",
                r"\bsabor(?:es)?\b",
                r"\bvalidade\b|\bvencimento\b|\bvence\b",
                r"\bgluten\b|\blactose\b|\bvegan\w*|\balergi\w*",
                r"\bcalorias?\b|\bkcal\b|\bnutricion\w*|\bproteinas?\b|\bacucar\b",
                r"\bgramas?\b|\bpeso\b|\bembalagem\b|\bcomposicao\b|\bproduto\b|\bformula\w*",
                r"\banvisa\b|\bregistro\b|\bcertificad\w*|\blote\b",
                r"\bcomo (?:tomar|consumir|usar)\b",
            )
        ),
    ),
    (
        "ARGUMENTO",
        _compile(
            (
                r"\bargument\w*",
                r"\bobjecao\b|\bobjecoes\b|\bconvenc\w*",
                r"\bdiferenciais?\b|\bconcorren\w*|\bcompar\w*",
                r"\bpitch\b|\bcomo vender\b|\bpor que (?:comprar|vender|escolher)\b",
                r"\blider\w*|\bshare\b|\bparticipacao\b|\bmercado\b|\bcrescendo\b",
            )
        ),
    ),
    (
        "MATERIAL",
        _compile(
            (
                r"\bmateriais?\b|\bcatalogo\b|\bapresentacao\b|\bfolder\b|\bpdf\b",
                r"\bbanner\b|\bfotos?\b|\bimagens?\b|\bvideos?\b|\bkit de vendas\b",
            )
        ),
    ),
    (
        "CAMPANHA",
        _compile(
            (
                r"\bcampanhas?\b|\bpromoc\w*|\bmix\b|\btrade\b|\blancamentos?\b",
                r"\bativacao\b|\bativacoes\b|\bdisplay\b|\bacao de\b|\bponto extra\b",
            )
        ),
    ),
    (
        "MIDIA",
        _compile(
            (
                r"\bmidias?\b|\banuncios?\b|\bpropaganda\b|\binfluenc\w*",
                r"\bredes sociais\b|\binstagram\b|\btiktok\b|\bcomercial de (?:tv|televisao)\b",
            )
        ),
    ),
)


def classify(text: str) -> str:
    """Classificacao por palavras-chave (4.7); preco vence; sem sinal: NAO_IDENTIFICADO."""
    if has_price_intent(text) or is_commercial_condition(text):
        return PRECO
    norm = _squash(text)
    best, best_hits = NAO_IDENTIFICADO, 0
    for name, pattern in _KEYWORDS:
        hits = len(pattern.findall(norm))
        if hits > best_hits:
            best, best_hits = name, hits
    return best


def urgency_for(outcome: BrainOutcome, classification: str | None) -> str:
    """Urgencia da tabela 4.7 (so vale para tickets escalados): urgente para PRECO e acesso."""
    if outcome == BrainOutcome.ACCESS_REQUEST or classification == PRECO:
        return URGENTE
    return POUCO_URGENTE


# --------------------------------------------------------------------------- guardas


def check_guards(text: str) -> GuardResult | None:
    """Guardas 1 (sem custo), em ordem; None segue para marca/recuperacao.

    O calculo fica em `check_calc`, pois so vale se nenhum item da fonte responder
    literalmente (decisao do provider).
    """
    if is_injection(text):
        return GuardResult(BrainOutcome.INJECTION, REASON_INJECTION, NAO_IDENTIFICADO)
    if is_greeting(text):
        return GuardResult(BrainOutcome.GREETING, None, NAO_IDENTIFICADO, thanks=is_thanks(text))
    if is_internal_topic(text):
        return GuardResult(BrainOutcome.REFUSAL, REASON_OUT_OF_SCOPE, NAO_IDENTIFICADO)
    if is_commercial_condition(text):
        return GuardResult(BrainOutcome.NO_ANSWER, REASON_COMMERCIAL, PRECO)
    return None


def check_calc(text: str) -> GuardResult | None:
    """`no_answer` PRECO para pedido de calculo de margem/preco ao consumidor."""
    if is_calc_intent(text):
        return GuardResult(BrainOutcome.NO_ANSWER, REASON_CALC, PRECO)
    return None
