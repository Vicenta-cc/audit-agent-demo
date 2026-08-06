import tempfile
import unittest
from pathlib import Path

from backend.audit_agent.auth_state_cipher import AuthStateCipher


class AuthStateCipherTest(unittest.TestCase):
    def test_generated_key_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "crawler_auth.key"
            auth_state = {
                "cookies": [{"name": "LOGIN_STATUS", "value": "1"}],
                "origins": [{"origin": "https://www.douyin.com", "localStorage": []}],
            }

            encrypted = AuthStateCipher(key_file=key_path).encrypt(auth_state)
            decrypted = AuthStateCipher(key_file=key_path).decrypt(encrypted)

            self.assertEqual(decrypted, auth_state)
            self.assertNotIn("LOGIN_STATUS", encrypted)
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)

    def test_configured_passphrase_is_stable(self):
        first = AuthStateCipher(encryption_key="deployment-secret")
        second = AuthStateCipher(encryption_key="deployment-secret")
        ciphertext = first.encrypt({"cookies": [], "origins": []})

        self.assertEqual(second.decrypt(ciphertext), {"cookies": [], "origins": []})


if __name__ == "__main__":
    unittest.main()
