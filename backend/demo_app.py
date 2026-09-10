"""Local demonstration entry point with verifiable deployment identity."""
import os
import sys
from pathlib import Path
from backend.main import app
from backend.audit_agent.config import settings

@app.get('/api/demo/identity')
def demo_identity():
    return {
        'code_dir': str(Path(__file__).resolve().parents[1]),
        'data_dir': str(settings.data_dir.resolve()),
        'python': sys.executable,
        'api_port': int(os.environ['XHS_AUDIT_BACKEND_PORT']),
        'web_port': int(os.environ['DEMO_WEB_PORT']),
    }
