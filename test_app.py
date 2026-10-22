import os
import unittest
from pathlib import Path

test_db_path = Path(__file__).parent / "test_data.sqlite3"
if test_db_path.exists():
    try:
        test_db_path.unlink()
    except Exception:
        pass

os.environ["DATABASE_PATH"] = str(test_db_path)

from fastapi.testclient import TestClient
import web
import db


class TestBlazeculturePTCPlatform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.client = TestClient(web.app)

    @classmethod
    def tearDownClass(cls):
        if test_db_path.exists():
            try:
                test_db_path.unlink()
            except Exception:
                pass

    def test_01_health_and_config(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True})

        res_config = self.client.get("/api/config")
        self.assertEqual(res_config.status_code, 200)
        data = res_config.json()
        self.assertEqual(data["coinsPerAd"], 2.0)
        self.assertEqual(data["minWithdrawalUsd"], 5.0)

    def test_02_user_registration_and_referral(self):
        # Register User A (Referrer)
        res_a = self.client.post("/api/auth/register", json={
            "email": "userA@example.com",
            "username": "userA",
            "password": "password123"
        })
        self.assertEqual(res_a.status_code, 200)
        data_a = res_a.json()
        self.assertTrue(data_a["ok"])
        self.assertIn("token", data_a)
        ref_code_a = data_a["user"]["referralCode"]

        # Register User B (Referred by User A)
        res_b = self.client.post("/api/auth/register", json={
            "email": "userB@example.com",
            "username": "userB",
            "password": "password123",
            "referralCode": ref_code_a
        })
        self.assertEqual(res_b.status_code, 200)
        data_b = res_b.json()

        # Verify User B is linked to User A
        user_b_db = db.get_user_by_username("userB")
        user_a_db = db.get_user_by_username("userA")
        self.assertEqual(user_b_db["referred_by"], user_a_db["id"])

    def test_03_user_login_and_me(self):
        res = self.client.post("/api/auth/login", json={
            "login": "userA@example.com",
            "password": "password123"
        })
        self.assertEqual(res.status_code, 200)
        token_a = res.json()["token"]

        res_me = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        self.assertEqual(res_me.status_code, 200)
        me_data = res_me.json()
        self.assertEqual(me_data["user"]["username"], "userA")

    def test_04_ad_watching_and_referral_commission(self):
        login_b = self.client.post("/api/auth/login", json={
            "login": "userB",
            "password": "password123"
        }).json()
        token_b = login_b["token"]

        watch_res = self.client.post("/api/user/watch-ad", headers={"Authorization": f"Bearer {token_b}"})
        self.assertEqual(watch_res.status_code, 200)
        session_id = watch_res.json()["sessionId"]

        claim_res = self.client.post("/api/user/claim-reward", json={"sessionId": session_id}, headers={"Authorization": f"Bearer {token_b}"})
        self.assertEqual(claim_res.status_code, 200)
        claim_data = claim_res.json()
        self.assertTrue(claim_data["ok"])
        self.assertEqual(claim_data["reward_coins"], 2.0)

        user_b_db = db.get_user_by_username("userB")
        self.assertEqual(user_b_db["balance_coins"], 2.0)

        user_a_db = db.get_user_by_username("userA")
        self.assertEqual(user_a_db["balance_coins"], 0.3)

    def test_05_premium_status_and_ad_bypass(self):
        user_a = db.get_user_by_username("userA")
        # Toggle userA premium
        db.set_user_premium_status(user_a["id"], True)

        login_a = self.client.post("/api/auth/login", json={"login": "userA", "password": "password123"}).json()
        token_a = login_a["token"]

        # Watch ad as premium user should bypass timer immediately
        watch_res = self.client.post("/api/user/watch-ad", headers={"Authorization": f"Bearer {token_a}"})
        self.assertEqual(watch_res.status_code, 200)
        data = watch_res.json()
        self.assertTrue(data["is_premium"])
        self.assertEqual(data["reward_coins"], 2.0)

    def test_06_channel_promotion_submission_and_approval(self):
        login_a = self.client.post("/api/auth/login", json={"login": "userA", "password": "password123"}).json()
        token_a = login_a["token"]

        # Submit Promotion
        sub_res = self.client.post("/api/promotions/submit", json={
            "channel_link": "https://t.me/testchannel",
            "ad_message": "Join testchannel now!",
            "budget_inr": 100.0,
            "payment_txn_ref": "TXN123456"
        }, headers={"Authorization": f"Bearer {token_a}"})
        self.assertEqual(sub_res.status_code, 200)
        promo_id = sub_res.json()["promotion"]["id"]

        # Admin login & approve
        admin_login = self.client.post("/api/admin/login", json={"username": "rajpapa", "password": "28@RajPapa"}).json()
        admin_token = admin_login["adminToken"]

        appr_res = self.client.post("/api/admin/promotions/approve", json={"promotionId": promo_id}, headers={"X-Admin-Token": admin_token})
        self.assertEqual(appr_res.status_code, 200)
        self.assertTrue(appr_res.json()["ok"])

    def test_07_connected_channels_and_revenue(self):
        login_b = self.client.post("/api/auth/login", json={"login": "userB", "password": "password123"}).json()
        token_b = login_b["token"]

        conn_res = self.client.post("/api/channels/connect", json={"channel_link": "https://t.me/myownerchannel"}, headers={"Authorization": f"Bearer {token_b}"})
        self.assertEqual(conn_res.status_code, 200)

        my_ch = self.client.get("/api/channels/my", headers={"Authorization": f"Bearer {token_b}"})
        self.assertEqual(my_ch.status_code, 200)
        self.assertEqual(len(my_ch.json()["channels"]), 1)

        # Simulate 2 joins on channel - should credit 0.50 INR (0.625 coins @ 80 INR/$ = 100 coins)
        user_b_before = db.get_user_by_username("userB")["balance_coins"]
        db.record_channel_join_event("https://t.me/myownerchannel", 1001)
        db.record_channel_join_event("https://t.me/myownerchannel", 1002)

        user_b_after = db.get_user_by_username("userB")["balance_coins"]
        self.assertAlmostEqual(user_b_after - user_b_before, 0.625)

    def test_08_withdrawal_flow(self):
        user_a = db.get_user_by_username("userA")
        db.adjust_user_balance(user_a["id"], 600.0)

        login_a = self.client.post("/api/auth/login", json={"login": "userA", "password": "password123"}).json()
        token_a = login_a["token"]

        withdraw_res = self.client.post("/api/user/withdraw", json={
            "amountUsd": 5.0,
            "method": "UPI",
            "accountDetails": "testuser@upi"
        }, headers={"Authorization": f"Bearer {token_a}"})

        self.assertEqual(withdraw_res.status_code, 200)
        self.assertTrue(withdraw_res.json()["ok"])

    def test_09_admin_panel_authentication_and_controls(self):
        admin_login = self.client.post("/api/admin/login", json={"username": "rajpapa", "password": "28@RajPapa"})
        self.assertEqual(admin_login.status_code, 200)
        admin_token = admin_login.json()["adminToken"]

        stats_res = self.client.get("/api/admin/stats", headers={"X-Admin-Token": admin_token})
        self.assertEqual(stats_res.status_code, 200)

        qr_res = self.client.post("/api/admin/payment-qr", json={
            "upi_id": "blaze@upi",
            "qr_image_url": "/static/logo.png"
        }, headers={"X-Admin-Token": admin_token})
        self.assertEqual(qr_res.status_code, 200)

    def test_10_telegram_mini_app_auto_login(self):
        # Auto-login Telegram user
        tg_res = self.client.post("/api/auth/telegram-mini-app", json={
            "telegram_id": 123456789,
            "username": "tg_user_test",
            "first_name": "Rajesh",
            "last_name": "Kumar",
            "photo_url": "https://example.com/photo.jpg"
        })
        self.assertEqual(tg_res.status_code, 200)
        data = tg_res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["user"]["telegram_id"], 123456789)
        self.assertEqual(data["user"]["first_name"], "Rajesh")
        tg_token = data["token"]

        # Call /api/auth/me using token
        me_res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tg_token}"})
        self.assertEqual(me_res.status_code, 200)
        me_data = me_res.json()
        self.assertEqual(me_data["user"]["telegram_id"], 123456789)
        self.assertEqual(me_data["user"]["first_name"], "Rajesh")

    def test_11_campaign_details_and_ad_watch_intent(self):
        # Create a campaign in DB
        token = "testcamp123"
        user_a = db.get_user_by_username("userA")
        db.create_campaign(token, "-1001234567", "Test Vip Channel", "direct", user_a["id"])

        # Fetch campaign details
        camp_res = self.client.get(f"/api/campaign/{token}")
        self.assertEqual(camp_res.status_code, 200)
        self.assertEqual(camp_res.json()["campaign"]["target_title"], "Test Vip Channel")

        # Telegram User logs in and watches ad for campaign
        tg_login = self.client.post("/api/auth/telegram-mini-app", json={
            "telegram_id": 987654321,
            "username": "tg_campaign_viewer"
        }).json()
        tg_token = tg_login["token"]
        tg_user_id = tg_login["user"]["id"]

        watch_res = self.client.post("/api/user/watch-ad", json={"campaignToken": token}, headers={"Authorization": f"Bearer {tg_token}"})
        self.assertEqual(watch_res.status_code, 200)
        session_id = watch_res.json()["sessionId"]

        claim_res = self.client.post("/api/user/claim-reward", json={"sessionId": session_id, "campaignToken": token}, headers={"Authorization": f"Bearer {tg_token}"})
        self.assertEqual(claim_res.status_code, 200)

        # Check rewarded intent recorded in DB
        has_intent = db.has_rewarded_intent(tg_user_id, token)
        self.assertTrue(has_intent)


if __name__ == "__main__":
    unittest.main()
