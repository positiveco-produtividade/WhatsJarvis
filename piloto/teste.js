// Testes do piloto técnico (PT-1 a PT-8, S-1 a S-7). Rodar: node piloto/teste.js
// Usa valores FALSOS de teste — nada aqui é chave de verdade.
'use strict';
process.env.WHATSAPP_TOKEN = 'token-falso';
process.env.APP_SECRET = 'segredo-de-teste';
process.env.VERIFY_TOKEN = 'verificacao-de-teste';
process.env.PHONE_NUMBER_ID = '111';
process.env.ALLOWED_NUMBERS = '5585900000000';
process.env.PORT = '0';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const http = require('node:http');

// O servidor não pode chamar a Meta durante o teste: troca o fetch por um falso que registra.
const enviados = [];
global.fetch = async (url, op) => { enviados.push(JSON.parse(op.body)); return { ok: true, json: async () => ({}) }; };

const { assinaturaValida, permitido, montarResposta, servidor } = require('./server.js');

let ok = 0;
const teste = async (nome, fn) => { await fn(); ok++; console.log('✓', nome); };
const assinar = (corpo) => 'sha256=' + crypto.createHmac('sha256', 'segredo-de-teste').update(corpo).digest('hex');
const pedir = (porta, metodo, caminho, corpo, cab = {}) => new Promise((resolve) => {
  const r = http.request({ host: '127.0.0.1', port: porta, method: metodo, path: caminho, headers: cab }, (res) => {
    let d = ''; res.on('data', (c) => (d += c)); res.on('end', () => resolve({ status: res.statusCode, corpo: d }));
  });
  if (corpo) r.write(corpo);
  r.end();
});
const evento = (de, texto, id, tipo = 'text') => JSON.stringify({ object: 'whatsapp_business_account', entry: [{ changes: [{ value: {
  metadata: { phone_number_id: '111' },
  messages: [{ from: de, id, timestamp: String(Math.floor(Date.now() / 1000)), type: tipo, ...(tipo === 'text' ? { text: { body: texto } } : {}) }],
} }] }] });
const espera = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await teste('S-1 assinatura certa passa, errada não', () => {
    const c = Buffer.from('{"a":1}');
    assert.equal(assinaturaValida(c, assinar(c)), true);
    assert.equal(assinaturaValida(c, 'sha256=' + '0'.repeat(64)), false);
    assert.equal(assinaturaValida(c, undefined), false);
  });
  await teste('S-3 lista positiva aceita com e sem o 9º dígito', () => {
    assert.equal(permitido('5585900000000'), true);
    assert.equal(permitido('558500000000'), true);
    assert.equal(permitido('5511999999999'), false);
  });
  await teste('PT-3 pergunta da FAQ é respondida e assinada pelo agente', () => {
    const r = montarResposta('jungle tem gluten?');
    assert.equal(r.status, 'respondido');
    assert.match(r.corpo, /sem glúten/);
    assert.match(r.corpo, /Duda · Jungle/);
  });
  await teste('PT-4 pergunta fora da FAQ recebe "não sei"', () => {
    assert.equal(montarResposta('qual o preço do proteico em PE?').status, 'nao_sei');
  });
  await teste('menu para "oi"', () => assert.equal(montarResposta('oi').status, 'menu'));

  await new Promise((r) => servidor.listen(0, '127.0.0.1', r));
  const porta = servidor.address().port;

  await teste('PT-1 verificação do webhook', async () => {
    assert.equal((await pedir(porta, 'GET', '/webhook?hub.mode=subscribe&hub.verify_token=verificacao-de-teste&hub.challenge=42')).corpo, '42');
    assert.equal((await pedir(porta, 'GET', '/webhook?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=42')).status, 403);
  });
  await teste('S-4 outros caminhos dão 404', async () => assert.equal((await pedir(porta, 'GET', '/admin')).status, 404));
  await teste('PT-2 POST sem assinatura válida é recusado e nada é enviado', async () => {
    const antes = enviados.length;
    const r = await pedir(porta, 'POST', '/webhook', evento('5585900000000', 'jungle tem gluten?', 'm0'), { 'x-hub-signature-256': 'sha256=' + 'a'.repeat(64) });
    assert.equal(r.status, 401); await espera(50); assert.equal(enviados.length, antes);
  });
  await teste('PT-3/PT-6 responde uma vez, mesmo se a Meta reenviar', async () => {
    const antes = enviados.length, c = evento('5585900000000', 'jungle tem gluten?', 'm1');
    await pedir(porta, 'POST', '/webhook', c, { 'x-hub-signature-256': assinar(c) });
    await pedir(porta, 'POST', '/webhook', c, { 'x-hub-signature-256': assinar(c) });
    await espera(100);
    assert.equal(enviados.length, antes + 1);
    assert.match(enviados.at(-1).text.body, /sem glúten/);
  });
  await teste('PT-5 número fora da lista não recebe nada', async () => {
    const antes = enviados.length, c = evento('5511999999999', 'oi', 'm2');
    await pedir(porta, 'POST', '/webhook', c, { 'x-hub-signature-256': assinar(c) });
    await espera(100); assert.equal(enviados.length, antes);
  });
  await teste('PT-8 áudio recebe "equipe avisada"', async () => {
    const antes = enviados.length, c = evento('5585900000000', '', 'm3', 'audio');
    await pedir(porta, 'POST', '/webhook', c, { 'x-hub-signature-256': assinar(c) });
    await espera(100); assert.equal(enviados.length, antes + 1);
    assert.match(enviados.at(-1).text.body, /Avisei a equipe/);
  });
  await teste('S-4 corpo acima de 64 KB é recusado', async () => {
    const r = await pedir(porta, 'POST', '/webhook', 'x'.repeat(70 * 1024), { 'x-hub-signature-256': 'sha256=00' });
    assert.ok([413, undefined].includes(r.status) || r.status === 413);
  });

  servidor.close();
  console.log(`\n${ok} testes passaram.`);
})().catch((e) => { console.error('✗ FALHOU:', e.message); process.exit(1); });
