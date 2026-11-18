// Processo PM2 do Jarvis (spec porta-fechada.1, 4.10; modelo do AUTO-1).
// Uso no servidor: pm2 start /opt/jarvis/deploy/ecosystem.config.js && pm2 save
//
// - create_app e uma fabrica: por isso `--factory` (sem ele o uvicorn nao acha `app`).
// - 1 worker de proposito: filas, monitor de sessao e laços de fundo rodam no processo.
// - So 127.0.0.1: quem fala com a internet e o nginx.
// - O .env e lido de /opt/jarvis/backend/.env (cwd), nunca versionado.
module.exports = {
  apps: [{
    name: "jarvis",
    script: "/opt/jarvis/venv/bin/uvicorn",
    args: "app.main:create_app --factory --host 127.0.0.1 --port 8010 --workers 1",
    cwd: "/opt/jarvis/backend",
    interpreter: "none",
    env: { PYTHONPATH: "/opt/jarvis/backend" },
    error_file: "/var/log/jarvis/error.log",
    out_file: "/var/log/jarvis/out.log",
    log_date_format: "YYYY-MM-DD HH:mm:ss",
    max_memory_restart: "300M",
    restart_delay: 3000,
  }]
};
