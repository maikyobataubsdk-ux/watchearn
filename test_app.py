import os
import unittest
from pathlib import Path

test_db_path = Path(__file__).parent / "test_data.sqlite3"
if test_db_path.exists():
    test_db_path.unlink()

# Set dummy env variables before importing app modules
os.environ["BOT_TOKEN"] = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
os.environ["ADMIN_USER_ID"] = "12345678"
os.environ["WEBAPP_URL"] = "https://miniapp.example.com"
os.environ["MONETAG_SDK_URL"] = "https://example.com/sdk.js"
os.environ["MONETAG_ZONE_ID"] = "12345"
os.environ["MONETAG_POSTBACK_SECRET"] = "testsecret123"
os.environ["DATABASE_PATH"] = str(test_db_path)

from fastapi.testclient import TestClient
from telegram_auth import validate_init_data, TelegramAuthError
from db import (
    init_db, create_campaign, get_campaign, create_ad_intent,
    handle_postback, save_unlock, get_unlock, has_unlock, has_rewarded_intent
)
import web


class TestTelegramAuth(unittest.TestCase):
    def test_validate_init_data_invalid_hash(self):
        invalid_init_data = "user=%7B%22id%22%3A12345%7D&auth_date=1600000000&hash=invalidhash"
        with self.assertRaises(TelegramAuthError):
            validate_init_data(invalid_init_data)

    def test_validate_init_data_empty(self):
        with self.assertRaises(TelegramAuthError):
            validate_init_data("")


class TestDatabaseAndAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        create_campaign("testtoken123", "-100123456789", "Test Target Channel", "direct", 12345678)
        cls.client = TestClient(web.app)

    @classmethod
    def tearDownClass(cls):
        if test_db_path.exists():
            try:
                test_db_path.unlink()
            except Exception:
                pass

    def test_campaign_created(self):
        camp = get_campaign("testtoken123")
        self.assertIsNotNone(camp)
        self.assertEqual(camp["target_title"], "Test Target Channel")

    def test_health_endpoint(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True})

    def test_config_endpoint(self):
        res = self.client.get("/api/config")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["zoneId"], "12345")
        self.assertTrue(data["adsConfigured"])

    def test_postback_handler(self):
        ymid = "testymid123456789"
        create_ad_intent(ymid, 99999, "testtoken123")

        # Test invalid secret
        res = self.client.get(f"/monetag/postback?secret=wrongsecret&ymid={ymid}&event_type=impression&reward_event_type=valued")
        self.assertEqual(res.status_code, 403)

        # Test valid postback
        res = self.client.get(f"/monetag/postback?secret=testsecret123&ymid={ymid}&event_type=impression&reward_event_type=valued")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, "ok")
        self.assertTrue(has_rewarded_intent(99999, "testtoken123"))

    def test_unlock_records(self):
        save_unlock(88888, "testtoken123", "https://t.me/+invitelink")
        self.assertTrue(has_unlock(88888, "testtoken123"))
        unlock = get_unlock(88888, "testtoken123")
        self.assertEqual(unlock["invite_link"], "https://t.me/+invitelink")


if __name__ == "__main__":
    unittest.main()
