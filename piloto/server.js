// Piloto técnico (PILOTO-TECNICO-1) — o Jarvis responde no WhatsApp de teste.
// DESCARTÁVEL: roda no computador do Caio, exposto por túnel. A versão de
// produção é Python/FastAPI no droplet (ADR 2026-10-20).
// Segurança: ver docs/tasks/piloto-tecnico.1/spec/spec.md, seção "Segurança" (S-1 a S-10).
'use strict';
const http = require('node:http');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { carregarBase, buscar, marcaDaPergunta } = require('./faq.js');

// ---------- configuração (chaves só no .env, nunca no código — PT-7) ----------
try { process.loadEnvFile(path.join(__dirname, '.env')); } catch { /* sem .env: falha abaixo */ }
const CFG = {
  token: process.env.WHATSAPP_TOKEN || '',
  appSecret: process.env.APP_SECRET || '',
  verifyToken: process.env.VERIFY_TOKEN || '',
  phoneNumberId: process.env.PHONE_NUMBER_ID || '',
  permitidos: (process.env.ALLOWED_NUMBERS || '').split(',').map((s) => s.replace(/\D/g, '')).filter(Boolean),
  porta: Number(process.env.PORT || 8787),
  graph: process.env.GRAPH_VERSION || 'v25.0',
  limiar: Number(process.env.LIMIAR || 0.55),
};
const faltando = ['token', 'appSecret', 'verifyToken', 'phoneNumberId'].filter((k) => !CFG[k]);
if (require.main === module && (faltando.length || !CFG.permitidos.length)) {
  console.error('Configuração incompleta no piloto/.env. Faltando:', [...faltando, ...(CFG.permitidos.length ? [] : ['ALLOWED_NUMBERS'])].join(', '));
  process.exit(1);
}

const LIMITE_CORPO = 64 * 1024;           // S-4
const LIMITE_POR_HORA = 60;               // S-5
const IDADE_MAXIMA_S = 10 * 60;           // S-6
const AGENTES = { Jungle: 'Duda · Jungle', 'Plant Power': 'Léo · Plant Power', Possible: 'Nico · Possible',
  'A Tal da Castanha': 'Rita · A Tal da Castanha', Zaya: 'Tati · Zaya', Todas: 'Jarvis' };

const base = carregarBase();
console.log(`FAQ carregada: ${base.length} perguntas.`);

// ---------- log (S-8: sem chaves, telefone mascarado) ----------
const LOG_DIR = path.join(__dirname, 'logs');
fs.mkdirSync(LOG_DIR, { recursive: true });
const mascarar = (n) => (n ? '•••' + String(n).slice(-4) : '?');
function registrar(evento) {
  const linha = JSON.stringify({ em: new Date().toISOString(), ...evento });
  fs.appendFile(path.join(LOG_DIR, 'conversas.jsonl'), linha + '\n', () => {});
  console.log(linha);
}

// ---------- S-1: assinatura da Meta ----------
function assinaturaValida(corpoBruto, cabecalho) {
  if (typeof cabecalho !== 'string' || !cabecalho.startsWith('sha256=')) return false;
  const esperado = crypto.createHmac('sha256', CFG.appSecret).update(corpoBruto).digest('hex');
  const recebido = cabecalho.slice(7);
  if (recebido.length !== esperado.length) return false;
  return crypto.timingSafeEqual(Buffer.from(recebido, 'hex'), Buffer.from(esperado, 'hex'));
}

// ---------- S-3: lista positiva (aceita com e sem o 9º dígito brasileiro) ----------
function variantes(num) {
  const d = String(num).replace(/\D/g, '');
  const v = new Set([d]);
  if (/^55\d{2}9\d{8}$/.test(d)) v.add(d.slice(0, 4) + d.slice(5)); // tira o 9
  if (/^55\d{2}\d{8}$/.test(d)) v.add(d.slice(0, 4) + '9' + d.slice(4)); // põe o 9
  return v;
}
const permitido = (num) => [...variantes(num)].some((v) => CFG.permitidos.some((p) => variantes(p).has(v)));
// A Meta entrega o remetente brasileiro sem o 9 (wa_id), mas o número de teste só envia para o
// número exatamente como foi cadastrado na lista de destinatários (com o 9). Responde para o cadastrado.
const destinoCadastrado = (num) => CFG.permitidos.find((p) => [...variantes(num)].some((v) => variantes(p).has(v))) || num;

// ---------- S-5 e S-6: limite por hora e idempotência ----------
const envios = new Map();   // número -> timestamps (ms)
const vistos = new Map();   // id da mensagem -> quando
function passouDoLimite(num) {
  const agora = Date.now();
  const lista = (envios.get(num) || []).filter((t) => agora - t < 3600e3);
  lista.push(agora);
  envios.set(num, lista);
  return lista.length > LIMITE_POR_HORA;
}
function jaVisto(id) {
  const agora = Date.now();
  for (const [k, t] of vistos) if (agora - t > 24 * 3600e3) vistos.delete(k);
  if (vistos.has(id)) return true;
  vistos.set(id, agora);
  return false;
}

// ---------- resposta ----------
const MENU = 'Oi! Eu sou o Jarvis, assistente do time comercial da Positive. 👋\n\n' +
  'Por enquanto respondo dúvidas de produto (ingredientes, validade, doses, sabores) de Jungle, Plant Power e Possible, ' +
  'dados de mercado da Nielsen (liderança, share e crescimento por estado) e quais produtos os clientes mais pedem.\n\n' +
  'Exemplos: "Jungle tem glúten?", "Quem lidera bebidas vegetais em CE?", "O que os clientes mais pedem de Plant Power em SP?"';
const NAO_SEI = 'Ainda não sei responder isso. Anotei a pergunta para a equipe. 🙏';
const MIDIA = 'Recebi seu arquivo, mas ainda não consigo ver áudios e fotos. Avisei a equipe, que já te responde por aqui.';

function montarResposta(texto) {
  // cumprimentos com letras repetidas ("oiii", "olaaa") e variações comuns
  const simples = texto.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
  if (/^(o+i+e*|ola+|opa+|salve|hey|hello|menu|ajuda|help|bom dia|boa tarde|boa noite|e ai+|eai+|tudo bem|blz)([\s!.,?]|$)/.test(simples)
      && simples.split(/\s+/).length <= 4 && !/jungle|plant|possible|castanha|zaya|ultracoffee|nitro|creatin|fibergut/.test(simples)) return { corpo: MENU, status: 'menu' };
  const achado = buscar(base, texto);
  if (!achado || achado.nota < CFG.limiar) return { corpo: NAO_SEI, status: 'nao_sei', nota: achado ? achado.nota : 0 };
  const assinatura = AGENTES[achado.item.marca] || AGENTES[marcaDaPergunta(texto)] || 'Jarvis';
  return { corpo: `${achado.item.a}\n\n— ${assinatura}`, status: 'respondido', nota: achado.nota, pergunta: achado.item.q, origem: achado.item.origem };
}

async function enviar(para, corpo) {
  const r = await fetch(`https://graph.facebook.com/${CFG.graph}/${CFG.phoneNumberId}/messages`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${CFG.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ messaging_product: 'whatsapp', to: para, type: 'text', text: { body: corpo.slice(0, 4000) } }),
    signal: AbortSignal.timeout(10000),
  });
  if (!r.ok) {
    let detalhe = '';
    try { const j = await r.json(); detalhe = j && j.error ? `${j.error.code} ${j.error.message}` : ''; } catch { /* ignora */ }
    throw new Error(`Meta respondeu ${r.status} ${detalhe}`.trim());
  }
}

async function tratarMensagem(msg, valor) {
  const inicio = Date.now();
  const de = msg.from;
  if (valor.metadata?.phone_number_id !== CFG.phoneNumberId) { registrar({ evento: 'numero_destino_errado' }); return; } // S-2
  if (!permitido(de)) { registrar({ evento: 'fora_da_lista', de: mascarar(de), tipo: msg.type }); return; }           // S-3
  if (jaVisto(msg.id)) { registrar({ evento: 'duplicada', de: mascarar(de) }); return; }                               // S-6
  if (Date.now() / 1000 - Number(msg.timestamp || 0) > IDADE_MAXIMA_S) { registrar({ evento: 'antiga', de: mascarar(de) }); return; }
  if (passouDoLimite(de)) { registrar({ evento: 'limite_hora', de: mascarar(de) }); return; }                          // S-5
  if (msg.type !== 'text') {                                                                                           // PT-8
    try { await enviar(destinoCadastrado(de), MIDIA); registrar({ evento: 'midia_equipe_avisada', de: mascarar(de), tipo: msg.type }); }
    catch (e) { registrar({ evento: 'falha_envio', de: mascarar(de), tipo: msg.type, erro: e.message }); }
    return;
  }
  const texto = String(msg.text?.body || '').slice(0, 2000);
  const resp = montarResposta(texto);
  try {
    await enviar(destinoCadastrado(de), resp.corpo);
    registrar({ evento: resp.status, de: mascarar(de), texto, pergunta: resp.pergunta, origem: resp.origem, nota: resp.nota && +resp.nota.toFixed(2), ms: Date.now() - inicio });
  } catch (e) {
    registrar({ evento: 'falha_envio', de: mascarar(de), erro: e.message, ms: Date.now() - inicio });              // S-9
  }
}

// ---------- servidor HTTP ----------
const servidor = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const fim = (codigo, corpo = '') => { res.writeHead(codigo, { 'Content-Type': 'text/plain' }); res.end(corpo); };

  if (url.pathname !== '/webhook') return fim(404);                                                                      // S-4

  if (req.method === 'GET') {                                                                                            // PT-1
    const ok = url.searchParams.get('hub.mode') === 'subscribe' && url.searchParams.get('hub.verify_token') === CFG.verifyToken;
    registrar({ evento: ok ? 'webhook_verificado' : 'verificacao_recusada' });
    return ok ? fim(200, url.searchParams.get('hub.challenge') || '') : fim(403);
  }
  if (req.method !== 'POST') return fim(405);

  const partes = [];
  let tamanho = 0;
  let abortado = false;
  req.on('data', (c) => {
    tamanho += c.length;
    if (tamanho > LIMITE_CORPO && !abortado) { abortado = true; fim(413); req.destroy(); }
  });
  req.on('data', (c) => { if (!abortado) partes.push(c); });
  req.on('end', () => {
    if (abortado) return;
    const bruto = Buffer.concat(partes);
    if (!assinaturaValida(bruto, req.headers['x-hub-signature-256'])) { registrar({ evento: 'assinatura_invalida' }); return fim(401); } // S-1
    fim(200); // responde rápido à Meta; processa depois
    let dados;
    try { dados = JSON.parse(bruto.toString('utf8')); } catch { return registrar({ evento: 'json_invalido' }); }
    if (dados.object !== 'whatsapp_business_account') return registrar({ evento: 'objeto_inesperado' });                // S-2
    for (const entrada of dados.entry || []) for (const mudanca of entrada.changes || []) {
      const valor = mudanca.value || {};
      for (const msg of valor.messages || []) tratarMensagem(msg, valor).catch((e) => registrar({ evento: 'erro', erro: e.message }));
    }
  });
  req.on('error', () => {});
});

if (require.main === module) {
  servidor.listen(CFG.porta, '127.0.0.1', () => console.log(`Jarvis (piloto) ouvindo em http://127.0.0.1:${CFG.porta}/webhook`)); // S-7
}

module.exports = { assinaturaValida, permitido, montarResposta, servidor, CFG };
