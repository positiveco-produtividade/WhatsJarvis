// Piloto técnico (PILOTO-TECNICO-1) — base de conhecimento e busca sem IA.
// Fontes: Guia de produtos (piloto/guia.json) + resumos Nielsen e GMR gerados
// pela tela Dados (docs/designs/dados/, fora do git: dado licenciado/interno).
// As regras de "só o que serve de argumento" são as mesmas do protótipo.
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const DADOS = path.join(__dirname, '..', 'docs', 'designs', 'dados');

const lerJson = (arquivo) => {
  try { return JSON.parse(fs.readFileSync(arquivo, 'utf8')); } catch { return null; }
};

const artO = (m) => (/^A Tal/.test(m) ? m : 'O ' + m);
const artDo = (m) => (/^A Tal/.test(m) ? 'da ' : 'do ') + m;
const num = (v, d = 1) => v.toFixed(d).replace('.', ',');
const nomeMarca = (s) => String(s).toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());
const nomeProduto = (s) => String(s).toLowerCase().replace(/(^|[\s/(])\S/g, (c) => c.toUpperCase())
  .replace(/\b(\d+)X(\d)/gi, '$1x$2').replace(/\bAtdc\b/g, 'ATDC');
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

function itensNielsen() {
  const j = lerJson(path.join(DADOS, 'nielsen-resumo.json'));
  if (!j) return [];
  const out = [];
  for (const r of j.linhas) {
    if (r.lojas != null && r.lojas < 10) continue; // amostra pequena: número instável
    const em = r.lugar === 'Brasil' ? 'no Brasil' : 'em ' + r.lugar;
    const fonte = `Nielsen Retail · ${r.cat} · ${r.lugar === 'Brasil' ? 'Total Brasil' : r.lugar} · ${r.periodo}`;
    const add = (q, a) => out.push({ marca: r.marca, q, a: `${a} (Fonte: ${fonte}.)`, origem: 'Nielsen' });
    const d = r.shAnt != null ? r.sh - r.shAnt : 0;
    if (r.pos === 1) add(`Quem lidera ${r.cat} ${em}?`, `${artO(r.marca)} é o líder de ${r.cat} ${em}, com ${num(r.sh)}% de share`);
    if (r.pos >= 2 && r.pos <= 3 && d > -0.5) add(`Qual o share ${artDo(r.marca)} ${em}?`, `${artO(r.marca)} é o ${r.pos}º em ${r.cat} ${em}, com ${num(r.sh)}% de participação em volume`);
    if (r.shAnt != null && d >= 0.5) add(`${artO(r.marca)} está crescendo ${em}?`, `Sim. ${artO(r.marca)} ganhou ${num(d)} ponto${d >= 1.95 ? 's' : ''} de share ${em} em relação a ${r.periodoAnt} (de ${num(r.shAnt)}% para ${num(r.sh)}%)`);
  }
  return out;
}

function itensGmr() {
  const j = lerJson(path.join(DADOS, 'gmr-resumo.json'));
  if (!j) return [];
  const per = j.periodo.replace(/(\d{4})-(\d\d)/g, (m, a, b) => MESES[+b - 1] + '/' + a.slice(2));
  const lista = (t) => t.map((p, i) => `${i + 1}º ${nomeProduto(p)}`).join('; ');
  const conj = (t) => [...t].sort().join('+');
  const br = Object.fromEntries((j.brasil || []).map((r) => [r.marca, conj(r.top)]));
  const CANAL = { 'VAREJO TRADICIONAL': 'no varejo tradicional', 'VAREJO ESPECIALIZADO': 'no varejo especializado', FOOD: 'no food service' };
  const out = [];
  const add = (r, em, local) => out.push({
    marca: r.marca,
    q: `Quais produtos ${artDo(r.marca)} os clientes mais pedem ${em}?`,
    a: `Os mais pedidos ${artDo(r.marca)} ${em} são: ${lista(r.top)} (Fonte: GMR · pedidos de clientes Positive · ${local} · ${per}.)`,
    origem: 'GMR',
  });
  for (const r of j.brasil || []) add(r, 'no Brasil', 'Brasil');
  for (const r of j.porCanal || []) add(r, CANAL[r.recorte] || 'no canal ' + r.recorte.toLowerCase(), r.recorte.toLowerCase());
  for (const r of j.porUF || []) {
    if (['Jungle', 'Plant Power'].includes(r.marca) && conj(r.top) !== br[r.marca]) add(r, 'em ' + r.recorte, r.recorte);
  }
  return out;
}

// ---------- busca sem IA ----------
const VAZIAS = new Set(('a o e de da do das dos em no na nos nas um uma uns umas para pra pro com sem por que qual quais ' +
  'quem como quando onde e é eh tem ter ser os as se ao aos me te voce vc tu eu ele ela isso esse essa este esta mais mesmo ' +
  'ja so tambem muito pouco hoje ainda la aqui ai entao sobre seu sua seus suas meu minha nosso nossa vocês voces').split(' '));
const SINONIMOS = { atal: 'castanha', tal: 'castanha', 'plantpower': 'plant', ppower: 'plant', isotonico: 'isotonicos',
  vegetal: 'vegetais', lider: 'lidera', liderando: 'lidera', vende: 'pedem', vendem: 'pedem', vendido: 'pedem', vendidos: 'pedem',
  'mais-vendido': 'pedem', cresceu: 'crescendo', cresce: 'crescendo', participacao: 'share', ingrediente: 'ingredientes',
  validade: 'validade', vencimento: 'validade', gluten: 'gluten', calorias: 'calorias', kcal: 'calorias' };
const UFS = new Set('ac al am ap ba ce df es go ma mg ms mt pa pb pe pi pr rj rn ro rr rs sc se sp to'.split(' '));

function normalizar(texto) {
  return String(texto).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9\s]/g, ' ');
}
function tokens(texto) {
  return normalizar(texto).split(/\s+/).filter(Boolean)
    .map((t) => SINONIMOS[t] || t)
    .map((t) => (t.length > 4 && t.endsWith('s') ? t.slice(0, -1) : t))
    .filter((t) => t.length > 2 && !VAZIAS.has(t)); // siglas de UF tratadas à parte (se, pe, to, ma…)
}
// UF só conta quando vem em maiúsculas ("CE") ou depois de em/no/na/do/da/de ("em ce").
function ufsDe(texto) {
  const achadas = new Set();
  for (const m of String(texto).matchAll(/\b([A-Z]{2})\b/g)) if (UFS.has(m[1].toLowerCase())) achadas.add(m[1].toLowerCase());
  for (const m of normalizar(texto).matchAll(/\b(?:em|no|na|do|da|de|pro|pra)\s+([a-z]{2})\b/g)) if (UFS.has(m[1])) achadas.add(m[1]);
  return achadas;
}

const MARCAS = [
  ['Jungle', /\bjungle\b/], ['Plant Power', /\bplant\b|plant ?power|ultracoffee|nitro|fibergut|defense|the one|protein|creatin|creapower/],
  ['Possible', /\bpossible\b/], ['A Tal da Castanha', /tal da castanha|\batal\b|castanha/], ['Zaya', /\bzaya\b/],
];
function marcaDaPergunta(texto) {
  const t = normalizar(texto);
  const achadas = MARCAS.filter(([, r]) => r.test(t)).map(([m]) => m);
  return achadas.length === 1 ? achadas[0] : null;
}

function carregarBase() {
  const guia = lerJson(path.join(__dirname, 'guia.json'));
  const itens = [
    ...(guia ? guia.itens.map((i) => ({ ...i, origem: 'Guia' })) : []),
    ...itensNielsen(),
    ...itensGmr(),
  ];
  return itens.map((i) => ({ ...i, tk: new Set(tokens(i.q)), ufs: ufsDe(i.q), brasil: / no Brasil\??$/.test(i.q) }));
}

// Pontua quanto da PERGUNTA cadastrada aparece na mensagem e exige:
// - a marca certa, quando a mensagem cita uma marca;
// - o mesmo estado, quando a pergunta cadastrada é de um estado (Nielsen/GMR).
function buscar(base, mensagem) {
  const tm = new Set(tokens(mensagem));
  if (!tm.size) return null;
  const marca = marcaDaPergunta(mensagem);
  const ufsMsg = ufsDe(mensagem);
  let melhor = null;
  for (const item of base) {
    if (marca && item.marca !== marca && item.marca !== 'Todas') continue;
    if (item.ufs.size && ![...item.ufs].some((u) => ufsMsg.has(u))) continue; // recorte de outro estado
    if (item.brasil && ufsMsg.size) continue; // pediu um estado: não responde com o Brasil
    let comum = 0;
    for (const t of item.tk) if (tm.has(t)) comum += 1;
    // nota simétrica (F1): cobre o que foi perguntado E o que a pergunta cadastrada diz
    const nota = (item.tk.size + tm.size) ? (2 * comum) / (item.tk.size + tm.size) : 0;
    if (!melhor || nota > melhor.nota) melhor = { item, nota };
  }
  return melhor;
}

module.exports = { carregarBase, buscar, marcaDaPergunta, tokens, ufsDe };
