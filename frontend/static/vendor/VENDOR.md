# Vendor local (CSP sem CDN, spec 4.4.4)

Arquivos servidos localmente; nenhum carregamento externo. Data de inclusao: 2026-10-26.
Alterar um arquivo exige atualizar o sha384 abaixo (conferido por `backend/tests/panel/unit/test_vendor_manifest.py`).

| file | library | version | origin | sha384 | date | note |
|---|---|---|---|---|---|---|
| htmx.min.js | htmx.org | 1.9.12 | https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js | ujb1lZYygJmzgSwoxRggbCHcjc0rB2XoQrxeTUQyRjrOnlCoYta87iKBWq3EsdM2 | 2026-10-26 | mesmo hash SRI do AUTO-1 |
| alpine-csp.min.js | @alpinejs/csp | 3.13.10 | https://unpkg.com/@alpinejs/csp@3.13.10/dist/cdn.min.js | QgB/TK/ayNq3A2XECXESGaaa8qrPwEjb4FCnbg7x8e110DNKEDy5A6rFcPC56DPw | 2026-10-26 | mesmo hash SRI do AUTO-1 |
| chart.min.js | chart.js | 4.4.6 | https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js | Sse/HDqcypGpyTDpvZOJNnG0TT3feGQUkF9H+mnRvic+LjR+K1NhTt8f51KIQ3v3 | 2026-10-26 | mesmo hash SRI do AUTO-1 |
| marked.min.js | marked | 13.0.0 | https://github.com/markedjs/marked | XSZM29+lHDRNr0vsG1lQoIsoTlDPZPAAFz59IbvvNOwcOeMcCmxCr21rn8WfRYZT | 2026-10-26 | copiado do AUTO-1 |
| purify.min.js | DOMPurify | 3.1.6 | https://github.com/cure53/DOMPurify/blob/3.1.6/LICENSE | +VfUPEb0PdtChMwmBcBmykRMDd+v6D/oFmB3rZM/puCMDYcIvF968OimRh4KQY9a | 2026-10-26 | copiado do AUTO-1 |
| inter-latin.woff2 | Inter (fonte variável, pesos 100-900) | v20 | https://fonts.gstatic.com/s/inter/v20/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1ZL7.woff2 | l0ql7Q1zqvX5klAVRwYjcMVCkCxg5dMTdghsIlC3pYkX6wwEHerh2bP/4yAao3Im | 2026-10-26 | mesma fonte do AUTO-1 (Google Fonts); subconjunto latin; licença SIL OFL 1.1 |
| inter-latin-ext.woff2 | Inter (fonte variável, pesos 100-900) | v20 | https://fonts.gstatic.com/s/inter/v20/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa25L7SUc.woff2 | r0EoKgCx3XyQjhJdTJ6FDE+8mEZrV95dM7cWvqLDUVdqzM44cq76qdOaYiA3sebS | 2026-10-26 | subconjunto latin-ext; licença SIL OFL 1.1 |

## Imagens dos agentes (`../img/bots/`)

Origem: `docs/designs/bots/`, otimizadas sem perda (Pillow, `optimize=True`). `bot-*` = corpo inteiro; `rosto-*` = rosto/busto.

| arquivo | agente | marca |
|---|---|---|
| bot-positive / rosto-positive | Jarvis | Positive (coordenador) |
| bot-jungle / rosto-jungle | Duda | Jungle |
| bot-plant / rosto-plant | Leo | Plant Power |
| bot-possible / rosto-possible | Nico | Possible |
| bot-atal / rosto-atal | Rita | A Tal da Castanha |
| bot-zaya / rosto-zaya | Tati | Zaya |
