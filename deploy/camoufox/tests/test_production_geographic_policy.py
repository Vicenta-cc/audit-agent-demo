"""Production geographic startup cannot silently disable established protection."""
from unittest.mock import patch

import pytest
from control import InvalidRelease, environment, validate
from test_control import manifest


def production(root):
    config = manifest(root)
    config['mode'] = 'production'
    config['geographic_runtime'] = True
    config['ports'].update(backend=8298, frontend=3298, allowed=[8298, 3298],
                           protected=[80, 443, 13298])
    return validate(config)


def test_required_protection_and_maintenance_share_release_environment(tmp_path):
    config = production(tmp_path)
    private = {'CRAWLER_SAFETY_ENABLED': 'true'}
    with patch('control.parent_private_environment', return_value=private):
        env = environment(config)
    assert env['CRAWLER_SAFETY_ENABLED'] == 'true'
    assert env['CRAWLER_BROWSER_MAINTENANCE'] == 'true'
    assert env['CRAWLER_LOGIN_V2_ENABLED'] == 'true'
    assert env['APP_AUTH_DB'] == config['runtime']['owner_db']
    assert env['REQUEST_SCHEDULER_DB'] == config['runtime']['scheduler_db']


@pytest.mark.parametrize(('key', 'value', 'error'), [
    ('APP_AUTH_MODE', 'disabled', 'auth_required'),
    ('CRAWLER_SAFETY_ENABLED', 'false', 'safety_required'),
    ('CRAWLER_DIRECT_DEGRADE_ENABLED', 'true', 'direct_degrade_forbidden'),
    ('APP_AUTH_COOKIE_SECURE', 'false', 'secure_cookie_required'),
])
def test_disabled_protection_rejected_before_process_configuration(tmp_path, key, value, error):
    config = production(tmp_path)
    private = {'CRAWLER_SAFETY_ENABLED': 'true', key: value}
    with patch('control.parent_private_environment', return_value=private):
        with pytest.raises(InvalidRelease, match=error):
            environment(config)


def test_missing_safety_policy_cannot_enable_geographic_production(tmp_path):
    with pytest.raises(InvalidRelease, match='safety_required'):
        environment(production(tmp_path))


def test_production_login_origin_must_be_https(tmp_path):
    config = production(tmp_path)
    config['environment']['CRAWLER_LOGIN_V2_ORIGINS'] = 'http://localhost:3399'
    with patch('control.parent_private_environment', return_value={'CRAWLER_SAFETY_ENABLED': 'true'}):
        with pytest.raises(InvalidRelease, match='https_origin_required'):
            environment(config)
