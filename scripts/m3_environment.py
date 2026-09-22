"""Fixed, complete M3 experiment; never discover or take over other runtimes."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import plistlib
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get('XHS_MANAGED_RUNTIME') or ROOT / 'outputs' / 'm3-environment').expanduser().resolve()
CONFIG = RUNTIME / 'config.json'


def configure():
    config = json.loads(CONFIG.read_text())
    formal = str(config.get('environment_id', '')).startswith('xhs-audit-formal')
    if formal and (RUNTIME == ROOT or ROOT in RUNTIME.parents):
        raise RuntimeError('正式数据目录必须独立于代码目录。')
    if Path(config['worktree']).resolve() != ROOT:
        raise RuntimeError('配置属于另一 worktree，拒绝启动。')
    data = Path(config['data_dir']).resolve()
    if data != (RUNTIME / 'data').resolve():
        raise RuntimeError('实验数据必须位于本 worktree 的独立运行目录。')
    if Path(config['crawler_dir']).resolve() != (RUNTIME / 'crawler').resolve():
        raise RuntimeError('采集目录必须属于本实验。')
    if config['api_port'] == config['frontend_port']:
        raise RuntimeError('前后端端口不能相同。')
    protected_ports = {8000, 3128, 8127, 3148, 8147, 8148, 8149, 3151, 8151, 3178, 8178} if formal else {8000, 3128, 8127, 3148, 8147, 8148, 8149, 3168, 8168}
    if any(int(config[k]) in protected_ports
           for k in ('api_port', 'frontend_port')):
        raise RuntimeError('拒绝使用其他环境的保留端口。')
    from dotenv import dotenv_values
    # Do not inherit provider/storage overrides from the invoking terminal.
    keep = {k: v for k, v in os.environ.items() if k in ('HOME', 'TMPDIR', 'LANG', 'USER')}
    environment = {**keep, **json.loads((RUNTIME / 'environment.json').read_text()),
                   **{k: v for k, v in dotenv_values(RUNTIME / 'secrets.env').items() if v is not None}}
    investigation_max_posts = int(environment.get('INVESTIGATION_MAX_POSTS') or 30)
    if not 1 <= investigation_max_posts <= 30:
        raise RuntimeError('INVESTIGATION_MAX_POSTS 必须在 1 到 30 之间。')
    environment.update(
        XHS_AUDIT_DATA_DIR=str(data), XHS_AUDIT_OUTPUTS_DIR=str(data / 'outputs'),
        HERMES_HOME=str(data / 'hermes'), CRAWLER_AUTH_KEY_FILE=str(data / 'crawler_auth.key'),
        CRAWLER_AUTH_ENCRYPTION_KEY='', MEDIACRAWLER_DIR=config['crawler_dir'],
        CRAWLER_LOGIN_PYTHON=str(Path(config['crawler_dir']) / '.venv/bin/python'),
        HERMES_CREATION_FAKE_RUNTIME='false',
        HISTORICAL_REPORT_A_DB=str(RUNTIME / 'archives/report-a.sqlite3'),
        HISTORICAL_REPORT_B_DB=str(RUNTIME / 'archives/report-b.sqlite3'),
        XHS_AUDIT_BACKEND_PORT=str(config['api_port']), PYTHONPATH=str(ROOT),
        VITE_API_PROXY_TARGET=f"http://127.0.0.1:{config['api_port']}",
        PATH=os.pathsep.join([str(Path(config['node']).parent), str(Path(config['python']).parent),
                             '/usr/bin', '/bin', '/usr/sbin', '/sbin', '/opt/homebrew/bin']),
        XHS_MANAGED_RUNTIME=str(RUNTIME),
        PYTHONDONTWRITEBYTECODE='1', INVESTIGATION_MAX_POSTS=str(investigation_max_posts),
        CRAWLER_MAX_CONCURRENCY='1', COMMENT_AUDIT_CONCURRENCY='1', VIDEO_REVIEW_CONCURRENCY='1')
    if formal:
        environment['HISTORICAL_REPORT_C_MANIFEST'] = str(RUNTIME / 'archives/report-c-manifest.json')
    os.environ.clear()
    os.environ.update(environment)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    return config


def identity(config):
    return {**{k: config[k] for k in ('environment_id', 'worktree', 'data_dir', 'api_port', 'frontend_port')},
            'build_hash': json.loads((RUNTIME / 'build.json').read_text())['source_hash']}


def source_hash():
    digest = hashlib.sha256()
    for directory in ('backend', 'hermes_m0', 'Audit_assistant/src', 'scripts'):
        for path in sorted((ROOT / directory).rglob('*')):
            if path.is_file() and path.suffix in {'.py', '.ts', '.tsx', '.js', '.mjs', '.sh', '.css', '.json', '.txt'}:
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def get_json(url):
    with urllib.request.urlopen(url, timeout=8) as response:
        return json.load(response)


def deleted_historical_workspaces(config):
    database = Path(config['data_dir']) / 'historical_report_demo.sqlite3'
    if not database.exists():
        return set()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deleted_workspaces'").fetchone():
            return set()
        return {row[0] for row in conn.execute('SELECT id FROM deleted_workspaces')}


def check(config):
    from backend.hermes_runtime.adapter import HermesRuntimeBinding
    from backend.historical_reports.catalog import HISTORICAL_REPORT_SPECS
    HermesRuntimeBinding()._verify_version()
    HermesRuntimeBinding().product_system_prompt()
    if not os.environ.get('DASHSCOPE_API_KEY'):
        raise RuntimeError('实验模型密钥未配置。')
    crawler = Path(config['crawler_dir'])
    if not (crawler / 'cache/abs_cache.py').is_file():
        raise RuntimeError('采集器 cache 源码包缺失，需补齐独立采集代码。')
    subprocess.run([str(crawler / '.venv/bin/python'), '-c',
                    'from media_platform.douyin import DouYinCrawler'],
                   cwd=crawler, stdout=subprocess.DEVNULL, check=True)
    for spec in HISTORICAL_REPORT_SPECS:
        if hashlib.sha256(spec.source_database.read_bytes()).hexdigest() != spec.source_database_sha256:
            raise RuntimeError('A/B 归档校验失败。')
    if not (ROOT / 'Audit_assistant/dist/index.html').is_file():
        raise RuntimeError('前端未构建；先运行 build。')
    if json.loads((RUNTIME / 'build.json').read_text())['source_hash'] != source_hash():
        raise RuntimeError('源代码已变化，先运行 build，再重启本实验环境。')
    database = Path(config['data_dir']) / 'audit_index.sqlite3'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
        ids = {r[0] for r in conn.execute('SELECT task_id FROM historical_report_imports')}
        deleted = deleted_historical_workspaces(config)
        required = {spec.task_id for spec in HISTORICAL_REPORT_SPECS if spec.workspace_id not in deleted}
        if not required <= ids:
            raise RuntimeError('未删除的历史报告未完整导入。')
    for filename in ('investigation.sqlite3', 'investigation_creation.sqlite3', 'crawler_auth.key'):
        if not (Path(config['data_dir']) / filename).is_file():
            raise RuntimeError('运行文件缺失：' + filename)
    print('代码、独立数据、Hermes、A/B、密钥存在性与前端构建检查通过。', flush=True)


def status(config, worker=True):
    api = f"http://127.0.0.1:{config['api_port']}"
    front = f"http://127.0.0.1:{config['frontend_port']}"
    expected = identity(config)
    for url in (api + '/api/m3-runtime', front + '/api/m3-runtime', front + '/__runtime'):
        if get_json(url) != expected:
            raise RuntimeError('前后端环境身份不一致。')
    reports = get_json(front + '/api/historical-report-workspaces')['items']
    required = {'historical-report-a', 'historical-report-b'}
    if str(config.get('environment_id', '')).startswith('xhs-audit-formal'):
        required.add('historical-report-c')
    required -= deleted_historical_workspaces(config)
    if not required <= {r['workspace_id'] for r in reports}:
        raise RuntimeError('A/B 会话不可用。')
    for report in reports:
        get_json(front + '/api/report-versions/' + report['report_version_id'])
    if worker:
        heartbeat = json.loads((RUNTIME / 'worker-status.json').read_text())
        if heartbeat['environment_id'] != expected['environment_id'] or time.time() - heartbeat['time'] > 15:
            raise RuntimeError('实验 worker 心跳不可用。')
        os.kill(heartbeat['pid'], 0)
    return {'url': front + '/investigation', 'reports': len(reports), 'identity': expected,
            'worker': 'ready' if worker else 'not_checked'}


def initialize(config):
    # A fresh target only. Never clone Run/Job queues from another environment.
    marker = RUNTIME / 'initialized.json'
    if marker.exists():
        raise RuntimeError('环境已初始化，拒绝覆盖；请使用 start/status。')
    from backend.main import historical_report_demo_service, principal_provider, ruleset_service
    from backend.rulesets.contracts import RuleSetContent
    from backend.rulesets.errors import RuleSetNotFoundError
    from backend.rulesets.trial_profiles import K2_RULESET_ID, TRIAL_BUNDLES
    reports = historical_report_demo_service.list_workspaces(principal_id=principal_provider().id)
    content = RuleSetContent.model_validate_json((TRIAL_BUNDLES[K2_RULESET_ID] / 'ruleset.json').read_text())
    try:
        draft = ruleset_service.get(K2_RULESET_ID, principal=principal_provider())
    except RuleSetNotFoundError:
        draft = ruleset_service.create_draft(content, principal=principal_provider(), ruleset_id=K2_RULESET_ID)
    ruleset_service.publish(K2_RULESET_ID, expected_revision=draft['draft_revision'],
                            idempotency_key='m3-environment-initial-k2', principal=principal_provider())
    marker.write_text(json.dumps({'reports': [r['id'] for r in reports],
                                 'source': 'immutable A/B archives; no copied task queues'}, indent=2))
    print('A/B 和 K2 已发布规则已导入独立实验库。')


def check_port_available(port):
    with socket.socket() as probe:
        # Reuse a closed listener's TIME_WAIT connections, never a live listener.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', int(port)))
        probe.listen(1)


def start(config):
    check(config)
    with (RUNTIME / 'supervisor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps(status(config), ensure_ascii=False))
            return
        for key in ('api_port', 'frontend_port'):
            check_port_available(config[key])
        children = []
        logs = RUNTIME / 'logs'
        logs.mkdir(exist_ok=True)
        def launch(component):
            with (logs / (component + '.log')).open('a') as log:
                process = subprocess.Popen([config['python'], __file__, '_' + component],
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                children.append(process)
        try:
            launch('api')
            launch('frontend')
            deadline = time.monotonic() + 90
            while True:
                if any(p.poll() is not None for p in children):
                    raise RuntimeError('服务退出，请查看运行日志。')
                try:
                    status(config, worker=False)
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.5)
            launch('worker')
            print('完整实验环境已启动：' + status(config, worker=False)['url'], flush=True)
            while all(p.poll() is None for p in children):
                time.sleep(1)
            raise RuntimeError('实验子进程退出。')
        finally:
            for p in reversed(children):
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
            for p in children:
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()


def service_action(config, action):
    label = 'com.xhs-audit.' + config['environment_id'].removeprefix('xhs-audit-') if str(config.get('environment_id', '')).startswith('xhs-audit-formal') else 'com.xhs-audit.m3-resource-experiment'
    domain = f'gui/{os.getuid()}'
    plist = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    arguments = [config['python'], str(ROOT / 'scripts/m3_environment.py'), 'start']
    if plist.exists() and plistlib.loads(plist.read_bytes()).get('ProgramArguments') != arguments:
        raise RuntimeError('同名系统服务属于其他环境，拒绝修改。')
    if action == 'install-service':
        check(config)
        (RUNTIME / 'logs').mkdir(exist_ok=True)
        payload = {'Label': label, 'ProgramArguments': arguments, 'WorkingDirectory': str(ROOT),
                   'EnvironmentVariables': {'XHS_MANAGED_RUNTIME': str(RUNTIME)},
                   'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 15,
                   'ExitTimeOut': 60, 'StandardOutPath': str(RUNTIME / 'logs/supervisor.log'),
                   'StandardErrorPath': str(RUNTIME / 'logs/supervisor.log')}
        plist.write_bytes(plistlib.dumps(payload))
        subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    elif action in {'stop', 'restart'}:
        if not plist.exists():
            raise RuntimeError('本实验尚未安装系统服务。')
        subprocess.run(['launchctl', 'bootout', domain + '/' + label], check=True)
        if action == 'restart':
            # Wait for graceful supervisor cleanup; never kill by port.
            deadline = time.monotonic() + 60
            while True:
                with (RUNTIME / 'supervisor.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('旧实验进程尚未退出；拒绝重启。')
                        time.sleep(0.5)
            check(config)
            subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    if action in {'install-service', 'restart'}:
        deadline = time.monotonic() + 90
        while True:
            try:
                ready = status(config)
                print('实验系统服务已就绪：' + ready['url'], flush=True)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError('实验系统服务未就绪，请查看 outputs/m3-environment/logs。') from None
                time.sleep(0.5)


def main():
    config = configure()
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action == 'init':
        initialize(config)
    elif action == 'check':
        check(config)
    elif action == 'status':
        print(json.dumps(status(config), ensure_ascii=False, indent=2))
    elif action == 'build':
        env = {k: v for k, v in os.environ.items() if k in ('HOME', 'TMPDIR', 'PATH', 'VITE_API_PROXY_TARGET')}
        for command in ([config['node'], 'node_modules/typescript/bin/tsc', '--noEmit'],
                        [config['node'], 'node_modules/vite/bin/vite.js', 'build']):
            subprocess.run(command, cwd=ROOT / 'Audit_assistant', env=env, check=True)
        (RUNTIME / 'build.json').write_text(json.dumps({'source_hash': source_hash(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}))
    elif action == 'start':
        start(config)
    elif action in {'install-service', 'restart', 'stop'}:
        service_action(config, action)
    elif action == '_api':
        import uvicorn
        from backend.main import app
        running_identity = identity(config)
        @app.get('/api/m3-runtime', include_in_schema=False)
        def runtime_identity():
            return running_identity
        uvicorn.run(app, host='127.0.0.1', port=config['api_port'], access_log=False)
    elif action == '_frontend':
        public = RUNTIME / 'public.json'
        public.write_text(json.dumps(identity(config)))
        env = {k: v for k, v in os.environ.items() if k in ('HOME', 'TMPDIR', 'PATH')}
        os.execve(config['node'], [config['node'], str(ROOT / 'scripts/m3_frontend.mjs'), str(public)], env)
    elif action == '_worker':
        from backend.investigation_creation.worker import build_worker
        worker = build_worker()
        def heartbeat():
            while True:
                payload = {'environment_id': config['environment_id'], 'pid': os.getpid(), 'time': time.time()}
                temp = RUNTIME / 'worker-status.tmp'
                temp.write_text(json.dumps(payload))
                temp.replace(RUNTIME / 'worker-status.json')
                time.sleep(2)
        threading.Thread(target=heartbeat, daemon=True).start()
        worker.run_forever(poll_seconds=1)
    else:
        raise RuntimeError('支持 init/check/build/start/status。')


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        main()
    except KeyboardInterrupt:
        pass
