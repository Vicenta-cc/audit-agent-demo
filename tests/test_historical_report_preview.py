"""Application wiring for ordinary previews must not import demo archives."""
import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ('auth_mode', 'demo_flag', 'expected_auto', 'expected_status'),
    [('disabled', None, False, 200), ('disabled', 'true', True, 503),
     ('required', 'true', False, 200)],
)
def test_main_history_registration_is_explicit(tmp_path, auth_mode, demo_flag, expected_auto, expected_status):
    env = dict(os.environ, APP_AUTH_MODE=auth_mode,
               XHS_AUDIT_DATA_DIR=str(tmp_path / 'data'),
               XHS_AUDIT_OUTPUTS_DIR=str(tmp_path / 'outputs'),
               APP_AUTH_DB=str(tmp_path / 'auth.sqlite3'),
               HISTORICAL_REPORT_A_DB=str(tmp_path / 'missing-a.sqlite3'),
               HISTORICAL_REPORT_B_DB=str(tmp_path / 'missing-b.sqlite3'),
               CRAWLER_LOGIN_V2_ENABLED='false', HERMES_CREATION_FAKE_RUNTIME='true')
    env.pop('HISTORICAL_REPORT_DEMO_AUTO_REGISTER', None)
    if demo_flag is not None:
        env['HISTORICAL_REPORT_DEMO_AUTO_REGISTER'] = demo_flag
    script = '''
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import main
from backend.api.historical_reports import create_historical_report_router
from backend.investigation_creation.principal import LocalPrincipalProvider
app = FastAPI()
app.include_router(create_historical_report_router(main.historical_report_demo_service, principal_provider=LocalPrincipalProvider("preview")))
with TestClient(app) as client:
    response = client.get("/api/historical-report-workspaces")
print(json.dumps({"auto": main.historical_report_demo_service.auto_register, "status": response.status_code, "body": response.json()}))
'''
    result = subprocess.run([sys.executable, '-c', script], env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload['auto'] is expected_auto
    assert payload['status'] == expected_status
    if expected_status == 200:
        assert payload['body'] == {'items': []}
    else:
        assert payload['body']['detail'] == '历史报告暂时无法读取，请联系管理员检查历史归档。'
        assert str(tmp_path) not in json.dumps(payload)
        assert 'HistoricalReportImportError' in result.stderr
        assert str(tmp_path / 'missing-a.sqlite3') not in result.stderr
