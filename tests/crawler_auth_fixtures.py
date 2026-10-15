"""Explicit trusted-login fixture boundary; generic auth backups stay untrusted."""


def mark_camoufox_login_verified(store, account_id):
    """Model the owner callback only for a fresh account's current device."""
    with store._connect() as db:
        row = db.execute('SELECT platform,profile_engine,profile_generation,auth_state_ciphertext FROM crawler_accounts WHERE id=?',
                         (account_id,)).fetchone()
        assert row and row['platform']=='dy' and row['profile_engine']=='camoufox'
        assert row['profile_generation'] and row['auth_state_ciphertext']
        db.execute('UPDATE crawler_accounts SET auth_source_generation=profile_generation WHERE id=?', (account_id,))
    return store.get(account_id)
