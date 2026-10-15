import fcntl
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_login_operation_store import CrawlerLoginOperationStore
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_browser_migration import BrowserMigrationService, MigrationError, profile_key
from scripts.crawler_browser_migrate import main


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = CrawlerAccountStore(self.root / 'accounts.sqlite3')
        self.account = self.store.create(platform='dy', display_name='fixture', platform_account_id='original-dy')
        self.aid = self.account['id']
        self.owner_db = self.root / 'owners.sqlite3'
        with sqlite3.connect(self.owner_db) as conn:
            conn.executescript("CREATE TABLE app_users(id TEXT,role TEXT,status TEXT); CREATE TABLE crawler_account_owners(account_id TEXT,owner_user_id TEXT);")
            conn.executemany('INSERT INTO app_users VALUES(?,?,?)', [('owner','user','active'),('other','user','active'),('admin','admin','active')])
            conn.execute('INSERT INTO crawler_account_owners VALUES(?,?)', (self.aid, 'owner'))
        self.scheduler = self.root / 'scheduler.sqlite3'
        with sqlite3.connect(self.scheduler) as conn:
            conn.execute('CREATE TABLE budget(scope TEXT,balance INT)')
            conn.execute("INSERT INTO budget VALUES('original',17)")
        self.old = self.root / 'logical' / profile_key(self.aid)
        self.old.mkdir(parents=True)
        (self.old / 'Cookies').write_text('never decrypt')
        self.initializations = 0
        self.service = self.make_service()

    def initialize(self, aid, *, root, generation):
        directory = root / profile_key(aid)
        directory.mkdir(parents=True, exist_ok=True)
        identity = directory / 'launch-identity.json'
        if not identity.exists():
            self.initializations += 1
            identity.write_text(json.dumps({'fixed': generation}))
            digest = hashlib.sha256(json.dumps({'fixed': generation},sort_keys=True,separators=(',', ':')).encode()).hexdigest()
            (directory / 'profile.json').write_text(json.dumps({'engine':'camoufox','generation':generation,'account_key':profile_key(aid),'identity_hash':digest}))
        return {'directory': str(directory), 'engine': 'camoufox', 'generation': generation}

    def make_service(self, **kw):
        return BrowserMigrationService(self.store, owner_db_path=self.owner_db,
            logical_root=self.root / 'logical', state_root=self.root / 'state',
            archive_root=self.root / 'archives', scheduler_db_path=self.scheduler,
            initializer=self.initialize, process_probe=lambda: '', **kw)

    def planned(self):
        return self.service.plan(self.aid, actor_id='owner', dry_run=False)

    def applied(self):
        row = self.planned()
        return self.service.apply(row['migration_id'], actor_id='owner', dry_run=False)

    def logged_in(self):
        row = self.applied()
        self.service.begin_login(row['migration_id'], actor_id='owner', operation_id='scan-one')
        return self.service.verify_login(row['migration_id'], actor_id='owner', operation_id='scan-one',
            platform_account_id='original-dy', ciphertext='encrypted-new-auth',
            expected_auth_revision=row['expected_auth_revision'], generation=row['generation'])

    def route(self, row):
        return dict(browser_exit='1.2.3.4', http_exit='1.2.3.4', expected_exit='1.2.3.4',
            restart_verified=True, authority_verified=True, identity_hash=row['identity_hash'],
            generation=row['generation'], binding_revision=row['binding_revision'], config_revision=row['config_revision'])

    def test_inventory_and_plan_default_are_read_only_and_no_secrets(self):
        before = self.store.db_path.read_bytes()
        inventory = self.service.inventory()
        self.assertEqual(inventory['accounts'][0]['owner_id'], 'owner')
        self.assertNotIn('ciphertext', json.dumps(inventory))
        self.assertTrue(self.service.plan(self.aid, actor_id='owner')['dry_run'])
        self.assertEqual(before, self.store.db_path.read_bytes())
        self.assertIsNone(self.service.status(account_id=self.aid))
        self.assertFalse((self.root / 'state').exists())

    def test_owner_and_admin_and_owner_change(self):
        with self.assertRaisesRegex(MigrationError, 'FORBIDDEN'):
            self.service.plan(self.aid, actor_id='other', dry_run=False)
        row = self.service.plan(self.aid, actor_id='admin', dry_run=False)
        with sqlite3.connect(self.owner_db) as conn:
            conn.execute("UPDATE crawler_account_owners SET owner_user_id='other'")
        with self.assertRaisesRegex(MigrationError, 'OWNER_CHANGED'):
            self.service.apply(row['migration_id'], actor_id='admin', dry_run=False)

    def test_missing_platform_identity_cannot_bind_by_scanning(self):
        with self.store._connect() as conn:
            conn.execute("UPDATE crawler_accounts SET platform_account_id='' WHERE id=?", (self.aid,))
        with self.assertRaisesRegex(MigrationError, 'PLATFORM_IDENTITY_UNKNOWN'):
            self.planned()

    def test_apply_archive_auth_fence_and_stable_identity(self):
        row = self.applied()
        self.assertEqual(row['phase'], 'LOGIN_REQUIRED')
        self.assertFalse(self.old.exists())
        self.assertEqual((Path(row['archive_path']) / 'Cookies').read_text(), 'never decrypt')
        self.assertEqual(self.store.get(self.aid)['auth_state_revision'], 1)
        self.assertEqual(self.service.resume(row['migration_id'], actor_id='owner', dry_run=False), row)
        self.assertEqual(self.initializations, 1)
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_BLOCKED'):
            self.service.guard(self.aid)
        with self.assertRaisesRegex(MigrationError, 'AUTH_SOURCE'):
            self.service.validate_auth_source(self.aid, generation=row['generation'], engine='camoufox')

    def test_crash_at_every_phase_and_fs_boundary_is_restartable(self):
        # A fresh isolated account/DB for each failpoint also exercises effects
        # committed before their DB receipt and rename before phase commit.
        for point in ('ADMISSION_BLOCKED','DRAINING','snapshot:intent','snapshot:effect','snapshot:receipt',
                      'SNAPSHOTTED','archive:intent','archive:effect','archive:receipt','ARCHIVED',
                      'profile:intent','profile:effect','profile:receipt','PROFILE_CREATED','LOGIN_REQUIRED'):
            with self.subTest(point=point):
                self.tearDown_fixture_for_repeat()
                row = self.planned()
                def crash(actual):
                    if actual == point:
                        raise RuntimeError('simulated crash')
                service = self.make_service(fault_hook=crash)
                with self.assertRaises(RuntimeError):
                    service.apply(row['migration_id'], actor_id='owner', dry_run=False)
                # No filesystem failure may release the fence.
                with self.assertRaisesRegex(MigrationError, 'MIGRATION_BLOCKED'):
                    self.service.guard(self.aid)
                resumed = self.service.resume(row['migration_id'], actor_id='owner', dry_run=False)
                self.assertEqual(resumed['phase'], 'LOGIN_REQUIRED')
                self.assertEqual(self.initializations, 1)
                self.assertEqual(self.store.get(self.aid)['auth_state_revision'], 1)

    def tearDown_fixture_for_repeat(self):
        self.setUp()

    def test_live_account_lock_blocks_without_boolean_bypass(self):
        row = self.planned()
        lock = self.root / 'logical' / '.resource-locks' / (profile_key(self.aid) + '.lock')
        lock.parent.mkdir()
        with lock.open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(MigrationError, 'ACCOUNT_LOCK_BUSY'):
                self.service.apply(row['migration_id'], actor_id='owner', dry_run=False)
        self.assertTrue(self.old.exists())

    def test_active_route_or_profile_process_blocks(self):
        row = self.planned()
        with self.store._connect() as conn:
            conn.execute("INSERT INTO crawler_route_admissions(operation_id,account_id,exit_key,generation,created_at) VALUES('busy',?,'exit',0,0)", (self.aid,))
        with self.assertRaisesRegex(MigrationError, 'ROUTE_NOT_DRAINED'):
            self.service.apply(row['migration_id'], actor_id='owner', dry_run=False)
        with self.store._connect() as conn:
            conn.execute("UPDATE crawler_route_admissions SET state='ended'")
        self.service.process_probe = lambda: 'firefox --profile ' + str(self.old)
        with self.assertRaisesRegex(MigrationError, 'PROFILE_PROCESS_ACTIVE'):
            self.service.apply(row['migration_id'], actor_id='owner', dry_run=False)

    def test_foreign_new_profile_or_symlink_never_overwritten(self):
        new = self.root / 'state' / 'profiles' / profile_key(self.aid)
        new.mkdir(parents=True)
        with self.assertRaisesRegex(MigrationError, 'ALREADY_EXISTS'):
            self.planned()
        new.rmdir()
        new.symlink_to(self.old)
        with self.assertRaisesRegex(MigrationError, 'UNSAFE_PATH'):
            self.service.inventory()

    def test_wrong_scan_and_revision_cannot_write_auth(self):
        row = self.applied()
        self.service.begin_login(row['migration_id'], actor_id='owner', operation_id='scan')
        base = dict(actor_id='owner', operation_id='scan', platform_account_id='wrong-dy',
                    ciphertext='cipher', expected_auth_revision=row['expected_auth_revision'], generation=row['generation'])
        with self.assertRaisesRegex(MigrationError, 'IDENTITY_CONFLICT'):
            self.service.verify_login(row['migration_id'], **base)
        base.update(platform_account_id='original-dy', expected_auth_revision=0)
        with self.assertRaisesRegex(MigrationError, 'REVISION_CONFLICT'):
            self.service.verify_login(row['migration_id'], **base)
        self.assertFalse(self.store.get(self.aid)['has_auth_state'])

    def test_login_operation_exception_is_exact_and_owner_bound(self):
        row = self.applied()
        self.service.begin_login(row['migration_id'], actor_id='owner', operation_id='scan')
        self.service.guard(self.aid, owner_id='owner', migration_id=row['migration_id'], operation_id='scan')
        for kwargs in [dict(owner_id='other',migration_id=row['migration_id'],operation_id='scan'),
                       dict(owner_id='owner',migration_id=row['migration_id'],operation_id='old'),
                       dict(owner_id='owner',migration_id='wrong',operation_id='scan')]:
            with self.assertRaisesRegex(MigrationError, 'MIGRATION_BLOCKED'):
                self.service.guard(self.aid, **kwargs)

    def test_commit_preserves_original_disabled_risk_budget_and_binding(self):
        with self.store._connect() as conn:
            conn.execute("UPDATE crawler_accounts SET status='disabled',risk_count=4,failure_kind='challenge',pause_reason='ACCOUNT_CHALLENGE',route_state='challenge_paused',cooldown_until='2099-01-01' WHERE id=?", (self.aid,))
        row = self.logged_in()
        self.service.verify_route(row['migration_id'], actor_id='owner', receipt=self.route(row))
        committed = self.service.commit(row['migration_id'], actor_id='owner')
        self.assertEqual(committed['phase'], 'COMMITTED')
        self.service.guard(self.aid)
        actual = self.store.get(self.aid)
        self.assertEqual(actual['status'], 'disabled')
        self.assertEqual(actual['risk_count'], 4)
        self.assertEqual(actual['route_state'], 'challenge_paused')
        self.assertEqual(actual['cooldown_until'], '2099-01-01')
        with sqlite3.connect(self.scheduler) as conn:
            self.assertEqual(conn.execute('SELECT balance FROM budget').fetchone()[0], 17)
        self.assertEqual(self.service.commit(row['migration_id'], actor_id='owner')['phase'], 'COMMITTED')

    def test_route_revision_change_blocks_commit_and_retains_fence(self):
        row = self.logged_in()
        self.service.verify_route(row['migration_id'], actor_id='owner', receipt=self.route(row))
        with self.store._connect() as conn:
            conn.execute('UPDATE crawler_accounts SET binding_revision=binding_revision+1 WHERE id=?', (self.aid,))
        with self.assertRaisesRegex(MigrationError, 'ROUTE_REVISION_CONFLICT'):
            self.service.commit(row['migration_id'], actor_id='owner')
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_BLOCKED'):
            self.service.guard(self.aid)

    def test_tampered_identity_and_route_exit_rejected(self):
        row = self.logged_in()
        receipt = self.route(row)
        receipt['http_exit'] = '5.6.7.8'
        with self.assertRaisesRegex(MigrationError, 'ROUTE_RECEIPT_CONFLICT'):
            self.service.verify_route(row['migration_id'], actor_id='owner', receipt=receipt)
        (Path(row['new_profile_path']) / 'launch-identity.json').write_text('{"changed": true}')
        with self.assertRaisesRegex(MigrationError, 'PROFILE_IDENTITY_CONFLICT'):
            self.service.reconcile(row['migration_id'], actor_id='owner')


    def test_duplicate_login_callback_is_idempotent_and_cas_race_rejected(self):
        row = self.applied()
        self.service.begin_login(row['migration_id'], actor_id='owner', operation_id='scan')
        kwargs = dict(actor_id='owner', operation_id='scan', platform_account_id='original-dy',
            ciphertext='cipher', expected_auth_revision=row['expected_auth_revision'], generation=row['generation'])
        actual = self.service.verify_login(row['migration_id'], **kwargs)
        self.assertEqual(actual, self.service.verify_login(row['migration_id'], **kwargs))
        self.assertEqual(self.store.get(self.aid)['auth_state_revision'], 2)
        kwargs['ciphertext'] = 'different-cipher'
        with self.assertRaisesRegex(MigrationError, 'REPLAY_CONFLICT'):
            self.service.verify_login(row['migration_id'], **kwargs)
        with self.store._connect() as conn:
            conn.execute('UPDATE crawler_accounts SET auth_state_revision=auth_state_revision+1 WHERE id=?', (self.aid,))
        with self.assertRaisesRegex(MigrationError, 'AUTH_REVISION_CONFLICT'):
            self.service.commit(row['migration_id'], actor_id='owner')

    def test_reconcile_does_not_repair_missing_identity_or_write_files(self):
        row = self.applied()
        marker = Path(row['new_profile_path']) / 'migration-identity.json'
        marker.unlink()
        with self.assertRaisesRegex(MigrationError, 'PROFILE_IDENTITY_MISSING'):
            self.service.reconcile(row['migration_id'], actor_id='owner')
        self.assertFalse(marker.exists())

    def test_profile_changes_after_snapshot_do_not_get_archived(self):
        row = self.planned()
        def interrupt(point):
            if point == 'SNAPSHOTTED':
                (self.old / 'Cookies').write_text('changed after snapshot')
                raise RuntimeError('crash')
        service = self.make_service(fault_hook=interrupt)
        with self.assertRaises(RuntimeError):
            service.apply(row['migration_id'], actor_id='owner', dry_run=False)
        with self.assertRaisesRegex(MigrationError, 'PROFILE_CHANGED_AFTER_SNAPSHOT'):
            self.service.resume(row['migration_id'], actor_id='owner', dry_run=False)
        self.assertTrue(self.old.exists())

    def test_scheduler_replacement_blocks_migration(self):
        row = self.planned()
        replacement = self.root / 'new-scheduler.sqlite3'
        with sqlite3.connect(replacement) as conn:
            conn.execute('CREATE TABLE other(id INT)')
        replacement.replace(self.scheduler)
        with self.assertRaisesRegex(MigrationError, 'SCHEDULER_IDENTITY_CONFLICT'):
            self.service.apply(row['migration_id'], actor_id='owner', dry_run=False)

    def test_missing_archive_and_corrupt_backup_receipt_fail_closed(self):
        row = self.applied()
        (Path(row['archive_path']) / 'Cookies').write_text('tampered')
        with self.assertRaisesRegex(MigrationError, 'ARCHIVE_CONFLICT'):
            self.service.reconcile(row['migration_id'], actor_id='owner')
        (Path(row['archive_path']) / 'Cookies').write_text('never decrypt')
        snapshot = self.root / 'archives' / row['migration_id'] / 'metadata' / 'owners.sqlite3'
        snapshot.write_bytes(b'broken')
        with self.assertRaisesRegex(MigrationError, 'BACKUP_RECEIPT_CONFLICT'):
            self.service.reconcile(row['migration_id'], actor_id='owner')

    def begin_operation(self, operations, *, migration_id=None, key='one', owner='owner'):
        return operations.begin(account_id=self.aid, owner_user_id=owner,
            application_session_id='session', owner_token_hash='tokenhash', request_key_hash=key,
            request_fingerprint=key, migration_id=migration_id)

    def test_existing_account_login_resolves_only_authorized_pending_migration(self):
        row = self.applied()
        operations = CrawlerLoginOperationStore(self.store, AuthStateCipher('fixture'), migration_service=self.service)
        from backend.audit_agent.config import settings
        with patch.object(settings, 'crawler_browser_maintenance', True):
            operation = self.begin_operation(operations)
            self.assertEqual(operation['migration_id'], row['migration_id'])
            self.assertTrue(self.service.authorized_login(self.aid, owner_id='owner',
                migration_id=row['migration_id'], operation_id=operation['id']))
            self.assertEqual(self.begin_operation(operations)['id'], operation['id'])
            with self.assertRaisesRegex(MigrationError, 'FORBIDDEN'):
                self.begin_operation(operations, key='unauthorized', owner='other')

    def test_new_account_generation_and_old_operation_store_guard(self):
        account = self.store.get(self.aid)
        self.assertEqual(account['profile_engine'], 'camoufox')
        self.assertTrue(account['profile_generation'])
        self.assertFalse(account['auth_source_generation'])
        ops = CrawlerLoginOperationStore(self.store, AuthStateCipher('fixture'), migration_service=self.service)
        op = self.begin_operation(ops)
        ops.mark_stage(op['id'], 'profile_ready', profile_authority_established=1)
        with self.store._connect() as conn:
            conn.execute("UPDATE crawler_accounts SET profile_generation='different' WHERE id=?", (self.aid,))
        with self.assertRaisesRegex(ValueError, 'GENERATION_CONFLICT'):
            ops.commit_auth(op['id'], 'encrypted', 'original-dy')
        with self.store._connect() as conn:
            conn.execute("UPDATE crawler_accounts SET profile_engine='legacy',profile_generation='' WHERE id=?", (self.aid,))
        with self.assertRaisesRegex(ValueError, 'MIGRATION_REQUIRED'):
            self.begin_operation(ops, key='two')

    def test_operation_commit_and_migration_identity_commit_are_one_transaction(self):
        row = self.applied()
        ops = CrawlerLoginOperationStore(self.store, AuthStateCipher('fixture'), migration_service=self.service)
        # The existing authorized account page can resolve its own pending
        # migration without exposing a migration ID in the frontend.
        op = self.begin_operation(ops)
        self.assertEqual(op['migration_id'], row['migration_id'])
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_FORBIDDEN'):
            self.begin_operation(ops, migration_id=row['migration_id'], owner='other')
        op = self.begin_operation(ops, migration_id=row['migration_id'])
        ops.mark_stage(op['id'], 'profile_ready', profile_authority_established=1)
        with patch.object(self.service, 'fault_hook', side_effect=RuntimeError('crash after account CAS')):
            with self.assertRaises(RuntimeError):
                ops.commit_auth(op['id'], 'encrypted', 'original-dy')
        self.assertEqual(self.service.status(row['migration_id'])['phase'], 'LOGIN_REQUIRED')
        self.assertEqual(self.store.get(self.aid)['auth_state_revision'], row['expected_auth_revision'])
        self.assertFalse(self.store.get(self.aid)['has_auth_state'])
        result = ops.commit_auth(op['id'], 'encrypted', 'original-dy')
        self.assertEqual(result['stage'], 'committed')
        self.assertEqual(self.service.status(row['migration_id'])['phase'], 'IDENTITY_VERIFIED')
        self.assertEqual(self.store.get(self.aid)['auth_state_revision'], row['expected_auth_revision'] + 1)
        self.assertEqual(self.store.get(self.aid)['auth_source_generation'], row['generation'])
        self.assertEqual(ops.commit_auth(op['id'], 'encrypted', 'original-dy'), result)

    def test_operation_callback_started_before_fence_is_refused(self):
        ops = CrawlerLoginOperationStore(self.store, AuthStateCipher('fixture'), migration_service=self.service)
        op = self.begin_operation(ops)
        ops.mark_stage(op['id'], 'profile_ready', profile_authority_established=1)
        row = self.planned()
        self.service._phase(row['migration_id'], 'owner', 'ADMISSION_BLOCKED')
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_BLOCKED'):
            ops.commit_auth(op['id'], 'encrypted', 'original-dy')
        self.assertFalse(self.store.get(self.aid)['has_auth_state'])

    def test_fresh_scan_establishes_only_current_generation_auth_source(self):
        ops = CrawlerLoginOperationStore(self.store, AuthStateCipher('fixture'), migration_service=self.service)
        op = self.begin_operation(ops)
        ops.mark_stage(op['id'], 'profile_ready', profile_authority_established=1)
        result = ops.commit_auth(op['id'], 'encrypted', 'original-dy')
        self.assertEqual(result['result']['account']['auth_source_generation'], self.store.get(self.aid)['profile_generation'])
        self.assertEqual(self.store.get(self.aid)['auth_state_revision'], 1)

    def test_maintenance_is_store_side_and_only_scoped_migration_login_is_exempt(self):
        from backend.audit_agent.config import settings
        ops = CrawlerLoginOperationStore(self.store, AuthStateCipher('fixture'), migration_service=self.service)
        op = self.begin_operation(ops)
        ops.mark_stage(op['id'], 'profile_ready', profile_authority_established=1)
        with patch.object(settings, 'crawler_browser_maintenance', True):
            with self.assertRaisesRegex(ValueError, 'CRAWLER_BROWSER_MAINTENANCE'):
                self.begin_operation(ops, key='denied')
            with self.assertRaisesRegex(ValueError, 'CRAWLER_BROWSER_MAINTENANCE'):
                ops.commit_auth(op['id'], 'encrypted', 'original-dy')
            row = self.applied()
            permitted = self.begin_operation(ops, key='migration', migration_id=row['migration_id'])
            ops.mark_stage(permitted['id'], 'profile_ready', profile_authority_established=1)
            self.assertEqual(ops.commit_auth(permitted['id'], 'encrypted', 'original-dy')['stage'], 'committed')

    def test_cli_mutations_require_bound_quiescence_receipt_before_schema_changes(self):
        common = ['--account-db',str(self.store.db_path),'--owner-db',str(self.owner_db),
                  '--logical-root',str(self.root/'logical'),'--state-root',str(self.root/'state'),
                  '--archive-root',str(self.root/'archives'),'--scheduler-db',str(self.scheduler)]
        before = self.store.db_path.read_bytes()
        with patch('os.geteuid', return_value=0):
            with self.assertRaisesRegex(MigrationError, 'DEPLOYMENT_QUIESCENCE_RECEIPT_REQUIRED'):
                main(['plan', *common, '--account-id', self.aid, '--actor-id', 'owner', '--execute'])
        self.assertEqual(before, self.store.db_path.read_bytes())

    def test_public_pool_migration_uses_admin_role_without_assigning_private_owner(self):
        with sqlite3.connect(self.owner_db) as conn:
            conn.execute('DELETE FROM crawler_account_owners WHERE account_id=?', (self.aid,))
        with self.store._connect() as conn:
            conn.execute("UPDATE crawler_accounts SET access_scope='public' WHERE id=?", (self.aid,))
        item = self.service.inventory()['accounts'][0]
        self.assertIsNone(item['owner_id'])
        self.assertEqual(item['classification'], 'public_admin_required')
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_FORBIDDEN'):
            self.service.plan(self.aid, actor_id='owner', dry_run=False)
        row = self.service.plan(self.aid, actor_id='admin', dry_run=False)
        applied = self.service.apply(row['migration_id'], actor_id='admin', dry_run=False)
        self.assertEqual(applied['phase'], 'LOGIN_REQUIRED')
        self.assertEqual(self.store.get(self.aid)['access_scope'], 'public')
        with sqlite3.connect(self.owner_db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM crawler_account_owners').fetchone()[0], 0)
            conn.execute("UPDATE app_users SET role='user' WHERE id='admin'")
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_FORBIDDEN'):
            self.service.begin_login(row['migration_id'], actor_id='admin', operation_id='scan')

    def test_private_orphan_has_no_automatic_admin_owner_substitution(self):
        with sqlite3.connect(self.owner_db) as conn:
            conn.execute('DELETE FROM crawler_account_owners')
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_FORBIDDEN'):
            self.service.plan(self.aid, actor_id='admin', dry_run=False)
        self.assertEqual(self.service.inventory()['accounts'][0]['classification'], 'owner_unknown')

    def test_new_start_key_cannot_replace_live_migration_login_operation(self):
        row = self.applied()
        ops = CrawlerLoginOperationStore(self.store, AuthStateCipher('fixture'), migration_service=self.service)
        op = self.begin_operation(ops, migration_id=row['migration_id'])
        with self.assertRaisesRegex(MigrationError, 'MIGRATION_LOGIN_OPERATION_BUSY'):
            self.begin_operation(ops, key='conflicting-start', migration_id=row['migration_id'])
        self.assertEqual(self.service.status(row['migration_id'])['login_operation_id'], op['id'])
        self.assertEqual(len(ops.list(account_id=self.aid)), 1)
        ops.mark_stage(op['id'], 'cancelled')
        replacement = self.begin_operation(ops, key='retry', migration_id=row['migration_id'])
        self.assertEqual(self.service.status(row['migration_id'])['login_operation_id'], replacement['id'])

    def test_live_verify_cli_uses_internal_owner_proof_without_offline_receipt(self):
        row = self.applied()
        common = ['--account-db',str(self.store.db_path),'--owner-db',str(self.owner_db),
                  '--logical-root',str(self.root/'logical'),'--state-root',str(self.root/'state'),
                  '--archive-root',str(self.root/'archives'),'--scheduler-db',str(self.scheduler)]
        with patch('os.geteuid', return_value=0), \
             patch('scripts.crawler_browser_migrate._verify_deployment_quiescence') as deployment, \
             patch('backend.audit_agent.crawler_browser_verification.verify_migrated_account', return_value={'phase':'COMMITTED'}) as verify, \
             patch('builtins.print'):
            main(['live-verify', *common, '--migration-id', row['migration_id'], '--actor-id', 'owner', '--execute'])
        self.assertEqual(deployment.call_args.kwargs, {'offline':False})
        self.assertEqual(verify.call_args.args, (row['migration_id'],))
        self.assertEqual(verify.call_args.kwargs['actor_id'], 'owner')
        self.assertTrue(verify.call_args.kwargs['commit'])

    def test_cli_default_readonly_and_execute_requires_os_admin(self):
        common = ['--account-db',str(self.store.db_path),'--owner-db',str(self.owner_db),
                  '--logical-root',str(self.root/'logical'),'--state-root',str(self.root/'state'),
                  '--archive-root',str(self.root/'archives'),'--scheduler-db',str(self.scheduler)]
        with patch('builtins.print'):
            self.assertEqual(main(['inventory', *common]), 0)
        with patch('os.geteuid', return_value=501):
            with self.assertRaisesRegex(MigrationError, 'LOCAL_OS_ADMIN_REQUIRED'):
                main(['plan',*common,'--account-id',self.aid,'--actor-id','admin','--execute'])
