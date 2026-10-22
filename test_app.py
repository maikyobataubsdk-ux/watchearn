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


class TestAdEarnPTCPlatform(unittest.TestCase):
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
        token_b = data_b["token"]

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
        # User B logs in and watches an ad
        login_b = self.client.post("/api/auth/login", json={
            "login": "userB",
            "password": "password123"
        }).json()
        token_b = login_b["token"]

        watch_res = self.client.post("/api/user/watch-ad", headers={"Authorization": f"Bearer {token_b}"})
        self.assertEqual(watch_res.status_code, 200)
        session_id = watch_res.json()["sessionId"]

        # Claim reward for User B
        claim_res = self.client.post("/api/user/claim-reward", json={"sessionId": session_id}, headers={"Authorization": f"Bearer {token_b}"})
        self.assertEqual(claim_res.status_code, 200)
        claim_data = claim_res.json()
        self.assertTrue(claim_data["ok"])
        self.assertEqual(claim_data["reward_coins"], 2.0)

        # User B balance should now be 2.0 coins
        user_b_db = db.get_user_by_username("userB")
        self.assertEqual(user_b_db["balance_coins"], 2.0)

        # User A should have received 15% referral bonus (0.3 coins)
        user_a_db = db.get_user_by_username("userA")
        self.assertEqual(user_a_db["balance_coins"], 0.3)

    def test_05_withdrawal_flow(self):
        user_a = db.get_user_by_username("userA")
        # Give User A enough coins for withdrawal (600 coins = $6.00)
        db.adjust_user_balance(user_a["id"], 600.0)

        login_a = self.client.post("/api/auth/login", json={
            "login": "userA",
            "password": "password123"
        }).json()
        token_a = login_a["token"]

        withdraw_res = self.client.post("/api/user/withdraw", json={
            "amountUsd": 5.0,
            "method": "UPI",
            "accountDetails": "testuser@upi"
        }, headers={"Authorization": f"Bearer {token_a}"})

        self.assertEqual(withdraw_res.status_code, 200)
        self.assertTrue(withdraw_res.json()["ok"])

        # Check balance reduced by 500 coins (600 - 500 = 100)
        user_a_after = db.get_user_by_username("userA")
        self.assertEqual(user_a_after["balance_coins"], 100.0)

    def test_06_admin_panel_authentication_and_controls(self):
        # Fail invalid admin credentials
        fail_login = self.client.post("/api/admin/login", json={"username": "wrong", "password": "wrong"})
        self.assertEqual(fail_login.status_code, 401)

        # Success admin login with rajpapa / 28@RajPapa
        admin_login = self.client.post("/api/admin/login", json={"username": "rajpapa", "password": "28@RajPapa"})
        self.assertEqual(admin_login.status_code, 200)
        admin_token = admin_login.json()["adminToken"]

        # Admin stats
        stats_res = self.client.get("/api/admin/stats", headers={"X-Admin-Token": admin_token})
        self.assertEqual(stats_res.status_code, 200)
        self.assertGreaterEqual(stats_res.json()["totalUsersCount"], 2)

        # Save Adsterra / HilltopAds Ad Codes
        ad_code_res = self.client.post("/api/admin/ad-codes", json={
            "interstitial": "<script>console.log('Adsterra Interstitial');</script>",
            "popunder": "<script>console.log('HilltopAds Popunder');</script>",
            "banner": "<div>High Paying Banner Ad</div>"
        }, headers={"X-Admin-Token": admin_token})

        self.assertEqual(ad_code_res.status_code, 200)
        self.assertTrue(ad_code_res.json()["ok"])

        # Approve pending withdrawal
        w_res = self.client.get("/api/admin/withdrawals", headers={"X-Admin-Token": admin_token})
        withdrawals = w_res.json()["withdrawals"]
        self.assertGreaterEqual(len(withdrawals), 1)
        pending_id = withdrawals[0]["id"]

        approve_res = self.client.post("/api/admin/withdrawal/status", json={
            "withdrawalId": pending_id,
            "status": "approved"
        }, headers={"X-Admin-Token": admin_token})

        self.assertEqual(approve_res.status_code, 200)
        self.assertTrue(approve_res.json()["ok"])


if __name__ == "__main__":
    unittest.main()
