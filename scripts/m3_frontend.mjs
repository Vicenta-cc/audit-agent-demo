import http from 'node:http';
import { createReadStream, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const identity = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const dist = path.join(identity.worktree, 'Audit_assistant/dist');
const types = {'.html':'text/html; charset=utf-8', '.js':'text/javascript', '.css':'text/css', '.svg':'image/svg+xml', '.png':'image/png', '.ico':'image/x-icon', '.woff2':'font/woff2'};
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  if (pathname.startsWith('/api/') || pathname.startsWith('/outputs/')) {
    const upstream = http.request({host:'127.0.0.1', port:identity.api_port, method:req.method,
      path:req.url, headers:{...req.headers, host:`127.0.0.1:${identity.api_port}`}}, reply => {
      res.writeHead(reply.statusCode, reply.headers); reply.pipe(res);
    });
    upstream.on('error', () => {
      if (!res.headersSent) res.writeHead(502, {'Content-Type':'application/json'});
      res.end(JSON.stringify({detail:'后台未连接，请检查工作台启动状态。'}));
    });
    res.on('close', () => upstream.destroy()); req.pipe(upstream); return;
  }
  if (pathname === '/__runtime') {
    res.writeHead(200, {'Content-Type':'application/json', 'Cache-Control':'no-store'});
    res.end(JSON.stringify(identity)); return;
  }
  if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(405); res.end(); return; }
  let decoded;
  try { decoded = decodeURIComponent(pathname); } catch { res.writeHead(400); res.end(); return; }
  let file = path.resolve(dist, '.' + decoded);
  if (file !== dist && !file.startsWith(dist + path.sep)) { res.writeHead(403); res.end(); return; }
  try { if (!statSync(file).isFile()) file = path.join(dist, 'index.html'); }
  catch { if (path.extname(decoded)) { res.writeHead(404); res.end(); return; } file = path.join(dist, 'index.html'); }
  res.writeHead(200, {'Content-Type':types[path.extname(file)] || 'application/octet-stream', 'Cache-Control':'no-store'});
  if (req.method === 'HEAD') { res.end(); return; }
  if (file.endsWith('/index.html')) {
    const injection = `<script>window.M3_EXPECTED_ENVIRONMENT=${JSON.stringify(identity).replaceAll('<','\\u003c')};</script>`;
    res.end(readFileSync(file, 'utf8').replace('<head>', '<head>' + injection));
  } else createReadStream(file).pipe(res);
});
server.listen(identity.frontend_port, '127.0.0.1');
process.on('SIGTERM', () => server.close(() => process.exit(0)));
