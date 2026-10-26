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
        # Credit userA wallet with ₹200 (20000 paise) for testing promotion submission
        user_a = db.get_user_by_username("userA")
        db.credit_wallet_deposit(user_a["id"], 20000, "TEST_DEPOSIT", "TEST_ORDER_06", "Test deposit for promo")

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
            "qr_image_url": "/static/payment_qr.jpg"
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

    def test_12_admin_bot_config_and_qr_default(self):
        admin_login = self.client.post("/api/admin/login", json={"username": "rajpapa", "password": "28@RajPapa"}).json()
        admin_token = admin_login["adminToken"]

        bot_conf = self.client.get("/api/admin/bot-config", headers={"X-Admin-Token": admin_token})
        self.assertEqual(bot_conf.status_code, 200)

        save_bot = self.client.post("/api/admin/bot-config", json={
            "force_chat_title": "Test Force Channel",
            "target_invite_mode": "request"
        }, headers={"X-Admin-Token": admin_token})
        self.assertEqual(save_bot.status_code, 200)

        qr_res = self.client.get("/api/promotions/payment-qr")
        self.assertEqual(qr_res.status_code, 200)
        self.assertIn("payment_qr.jpg", qr_res.json()["qr_image_url"])

    def test_13_channel_normalization_and_admin_route(self):
        # Test normalize_telegram_channel
        u1, i1, c1 = web.normalize_telegram_channel("https://t.me/mytestchannel")
        self.assertEqual(u1, "https://t.me/mytestchannel")
        self.assertEqual(i1, "@mytestchannel")
        self.assertEqual(c1, "mytestchannel")

        u2, i2, c2 = web.normalize_telegram_channel("@mytestchannel")
        self.assertEqual(u2, "https://t.me/mytestchannel")
        self.assertEqual(i2, "@mytestchannel")
        self.assertEqual(c2, "mytestchannel")

        u3, i3, c3 = web.normalize_telegram_channel("mytestchannel")
        self.assertEqual(u3, "https://t.me/mytestchannel")
        self.assertEqual(i3, "@mytestchannel")

        u4, i4, c4 = web.normalize_telegram_channel("https://t.me/+AbCdEf123")
        self.assertEqual(u4, "https://t.me/+AbCdEf123")
        self.assertEqual(i4, "+AbCdEf123")

        # Test /admin route endpoint
        admin_res = self.client.get("/admin")
        self.assertEqual(admin_res.status_code, 200)
        self.assertIn("Blazeculture", admin_res.text)

        # Test analyze_channel with different link formats
        login_a = self.client.post("/api/auth/login", json={"login": "userA", "password": "password123"}).json()
        token_a = login_a["token"]

        analyze_res = self.client.post("/api/promotions/analyze", json={"channel_link": "@mytestchannel"}, headers={"Authorization": f"Bearer {token_a}"})
        self.assertEqual(analyze_res.status_code, 200)
        self.assertEqual(analyze_res.json()["channel_link"], "https://t.me/mytestchannel")

    def test_14_youpi_webhook_and_wallet(self):
        import hmac
        import hashlib
        import json
        import time

        tg_res = self.client.post("/api/auth/telegram-mini-app", json={
            "telegram_id": 555666777,
            "username": "wallet_test_user",
            "first_name": "WalletTester"
        })
        self.assertEqual(tg_res.status_code, 200)
        user_token = tg_res.json()["token"]

        # Check initial balance
        bal_res = self.client.get("/api/wallet/balance", headers={"Authorization": f"Bearer {user_token}"})
        self.assertEqual(bal_res.status_code, 200)
        self.assertEqual(bal_res.json()["balance_paise"], 0)
        self.assertEqual(bal_res.json()["balance_inr"], 0.0)

        # Prepare webhook request
        now_ts = str(int(time.time()))
        body_dict = {
            "event": "SUCCESS",
            "method": "UPI",
            "amount": 250.00,
            "utr": "UTR1122334455",
            "order_id": "WALLET-555666777-998877",
            "customer": {"name": "WalletTester", "phone": "9876543210"},
            "metadata": {"telegram_user_id": 555666777, "purpose": "WALLET_TOPUP"}
        }
        raw_body = json.dumps(body_dict).encode("utf-8")

        secret = "69447d0ed8ea359cba870ab6b792e716813dc2006522154b45268577f851c7c0"
        payload_to_sign = raw_body.decode("utf-8") + "." + now_ts
        sig = hmac.new(secret.encode("utf-8"), payload_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        # Send webhook
        wh_res = self.client.post(
            "/api/youpi-webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Timestamp": now_ts,
                "X-Gateway-Signature": sig
            }
        )
        self.assertEqual(wh_res.status_code, 200)
        self.assertEqual(wh_res.json(), {"received": True})

        # Verify updated balance
        bal_after = self.client.get("/api/wallet/balance", headers={"Authorization": f"Bearer {user_token}"})
        self.assertEqual(bal_after.status_code, 200)
        self.assertEqual(bal_after.json()["balance_paise"], 25000)
        self.assertEqual(bal_after.json()["balance_inr"], 250.0)

        # Verify transaction log
        txns_res = self.client.get("/api/wallet/transactions", headers={"Authorization": f"Bearer {user_token}"})
        self.assertEqual(txns_res.status_code, 200)
        txns = txns_res.json()["transactions"]
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["utr"], "UTR1122334455")
        self.assertEqual(txns[0]["amount_paise"], 25000)

        # Duplicate webhook order_id check (idempotency)
        wh_dup_res = self.client.post(
            "/api/youpi-webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Timestamp": now_ts,
                "X-Gateway-Signature": sig
            }
        )
        self.assertEqual(wh_dup_res.status_code, 200)
        bal_dup = self.client.get("/api/wallet/balance", headers={"Authorization": f"Bearer {user_token}"})
        self.assertEqual(bal_dup.json()["balance_paise"], 25000)

        # Test invalid signature
        wh_bad_sig = self.client.post(
            "/api/youpi-webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Timestamp": now_ts,
                "X-Gateway-Signature": "invalid_sig"
            }
        )
        self.assertEqual(wh_bad_sig.status_code, 401)

        # Test timestamp drift (> 2 hours)
        old_ts = str(int(time.time()) - 8000)
        old_payload_to_sign = raw_body.decode("utf-8") + "." + old_ts
        old_sig = hmac.new(secret.encode("utf-8"), old_payload_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        wh_old_ts = self.client.post(
            "/api/youpi-webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Timestamp": old_ts,
                "X-Gateway-Signature": old_sig
            }
        )
        self.assertEqual(wh_old_ts.status_code, 400)

    def test_15_category_promotions_and_wallet_deduction(self):
        # Register User C
        res_c = self.client.post("/api/auth/register", json={
            "email": "userC@example.com",
            "username": "userC",
            "password": "password123"
        })
        self.assertEqual(res_c.status_code, 200)
        token_c = res_c.json()["token"]
        user_c_db = db.get_user_by_username("userC")

        # 1. Attempt promotion submission with 0 wallet balance -> Expect 400 Insufficient Balance
        fail_promo = self.client.post("/api/promotions/submit", json={
            "channel_link": "https://t.me/techchannel",
            "ad_message": "Join best tech channel!",
            "budget_inr": 100.0,
            "category": "Tech & Coding",
            "is_category_specific": True
        }, headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(fail_promo.status_code, 400)
        self.assertIn("Insufficient wallet balance", fail_promo.json()["detail"])

        # 2. Deposit ₹150 INR into wallet
        db.credit_wallet_deposit(user_c_db["id"], 15000, "TEST_DEPOSIT_C", "ORDER_C_15", "Topup 150 INR")

        # 3. Submit 20% surcharge category promotion (₹100 + 20% = ₹120 total)
        succ_promo = self.client.post("/api/promotions/submit", json={
            "channel_link": "https://t.me/techchannel",
            "ad_message": "Join best tech channel!",
            "budget_inr": 100.0,
            "category": "Tech & Coding",
            "is_category_specific": True
        }, headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(succ_promo.status_code, 200)
        p_data = succ_promo.json()
        self.assertTrue(p_data["ok"])
        self.assertEqual(p_data["promotion"]["total_price_inr"], 120.0)
        self.assertEqual(p_data["promotion"]["category"], "Tech & Coding")

        # Check remaining wallet balance (₹150 - ₹120 = ₹30 INR = 3000 paise)
        bal_res = self.client.get("/api/wallet/balance", headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(bal_res.json()["balance_paise"], 3000)

    def test_16_category_channel_connection_and_rotation_helpers(self):
        login_c = self.client.post("/api/auth/login", json={"login": "userC", "password": "password123"}).json()
        token_c = login_c["token"]

        # Connect channel with category
        conn_res = self.client.post("/api/channels/connect", json={
            "channel_link": "https://t.me/mytechchannel",
            "category": "Tech & Coding"
        }, headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(conn_res.status_code, 200)
        self.assertEqual(conn_res.json()["channel"]["category"], "Tech & Coding")

        # Test db helper get_connected_channels_by_category
        cat_chans = db.get_connected_channels_by_category("Tech & Coding")
        self.assertTrue(len(cat_chans) >= 1)
        self.assertEqual(cat_chans[0]["category"], "Tech & Coding")

    def test_18_verify_channel_requirements(self):
        login_b = self.client.post("/api/auth/login", json={"login": "userB", "password": "password123"}).json()
        token_b = login_b["token"]

        from unittest.mock import patch, AsyncMock

        with patch.dict("os.environ", {"TESTING": "0"}), patch("config.BOT_TOKEN", "123456789:ABCDEFG"):
            # Case A: User not connected with Telegram
            fail_no_tg = self.client.post("/api/channels/connect", json={
                "channel_link": "@testchannel"
            }, headers={"Authorization": f"Bearer {token_b}"})
            self.assertEqual(fail_no_tg.status_code, 400)
            self.assertIn("Telegram account connected nahi hai", fail_no_tg.json()["detail"])

            # Login as user with telegram_id
            tg_res = self.client.post("/api/auth/telegram-mini-app", json={
                "telegram_id": 99887766,
                "username": "channel_owner_tg"
            })
            tg_token = tg_res.json()["token"]

            # Case B: Bot not admin in channel
            mock_bot_not_admin = AsyncMock()
            mock_bot_not_admin.get.side_effect = [
                # 1. getMe
                AsyncMock(json=lambda: {"ok": True, "result": {"id": 12345, "is_bot": True}}),
                # 2. getChat
                AsyncMock(json=lambda: {"ok": True, "result": {"id": -10011223344, "title": "My Channel", "username": "mychan"}}),
                # 3. getChatMember for bot
                AsyncMock(json=lambda: {"ok": True, "result": {"status": "member"}}),
            ]

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.return_value = mock_bot_not_admin
                res = self.client.post("/api/channels/connect", json={"channel_link": "@mychan"}, headers={"Authorization": f"Bearer {tg_token}"})
                self.assertEqual(res.status_code, 400)
                self.assertIn("Bot is channel me admin nahi hai", res.json()["detail"])

            # Case C: User not owner/admin in channel
            mock_user_not_owner = AsyncMock()
            mock_user_not_owner.get.side_effect = [
                # 1. getMe
                AsyncMock(json=lambda: {"ok": True, "result": {"id": 12345, "is_bot": True}}),
                # 2. getChat
                AsyncMock(json=lambda: {"ok": True, "result": {"id": -10011223344, "title": "My Channel", "username": "mychan"}}),
                # 3. getChatMember for bot
                AsyncMock(json=lambda: {"ok": True, "result": {"status": "administrator"}}),
                # 4. getChatMember for user
                AsyncMock(json=lambda: {"ok": True, "result": {"status": "member"}}),
            ]

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.return_value = mock_user_not_owner
                res = self.client.post("/api/channels/connect", json={"channel_link": "@mychan"}, headers={"Authorization": f"Bearer {tg_token}"})
                self.assertEqual(res.status_code, 400)
                self.assertIn("Aap sirf wahi channel add kar sakte hai jo aap own karte hai", res.json()["detail"])

            # Case D: Channel members < 100
            mock_low_members = AsyncMock()
            mock_low_members.get.side_effect = [
                # 1. getMe
                AsyncMock(json=lambda: {"ok": True, "result": {"id": 12345, "is_bot": True}}),
                # 2. getChat
                AsyncMock(json=lambda: {"ok": True, "result": {"id": -10011223344, "title": "My Channel", "username": "mychan"}}),
                # 3. getChatMember for bot
                AsyncMock(json=lambda: {"ok": True, "result": {"status": "administrator"}}),
                # 4. getChatMember for user
                AsyncMock(json=lambda: {"ok": True, "result": {"status": "creator"}}),
                # 5. getChatMemberCount
                AsyncMock(json=lambda: {"ok": True, "result": 42}),
            ]

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.return_value = mock_low_members
                res = self.client.post("/api/channels/connect", json={"channel_link": "@mychan"}, headers={"Authorization": f"Bearer {tg_token}"})
                self.assertEqual(res.status_code, 400)
                self.assertIn("Minimum member 100 required", res.json()["detail"])

            # Case E: Valid channel with >= 100 members, bot admin, user creator
            mock_valid = AsyncMock()
            mock_valid.get.side_effect = [
                # 1. getMe
                AsyncMock(json=lambda: {"ok": True, "result": {"id": 12345, "is_bot": True}}),
                # 2. getChat
                AsyncMock(json=lambda: {"ok": True, "result": {"id": -10011223344, "title": "Valid Big Channel", "username": "validchan"}}),
                # 3. getChatMember for bot
                AsyncMock(json=lambda: {"ok": True, "result": {"status": "administrator"}}),
                # 4. getChatMember for user
                AsyncMock(json=lambda: {"ok": True, "result": {"status": "creator"}}),
                # 5. getChatMemberCount
                AsyncMock(json=lambda: {"ok": True, "result": 1250}),
            ]

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.return_value = mock_valid
                res = self.client.post("/api/channels/connect", json={"channel_link": "@validchan"}, headers={"Authorization": f"Bearer {tg_token}"})
                self.assertEqual(res.status_code, 200)
                self.assertTrue(res.json()["ok"])
                self.assertEqual(res.json()["channel"]["channel_title"], "Valid Big Channel")

    def test_17_admin_add_funds_and_channel_deletion(self):
        login_c = self.client.post("/api/auth/login", json={"login": "userC", "password": "password123"}).json()
        token_c = login_c["token"]
        user_c = db.get_user_by_username("userC")

        # 1. Admin Login
        admin_login = self.client.post("/api/admin/login", json={"username": "rajpapa", "password": "28@RajPapa"}).json()
        admin_token = admin_login["adminToken"]

        # 2. Admin Add ₹500 Funds to User C Wallet
        add_res = self.client.post("/api/admin/user/add-funds", json={
            "userId": user_c["id"],
            "amountInr": 500.0,
            "description": "Bonus Grant from Master Admin"
        }, headers={"X-Admin-Token": admin_token})
        self.assertEqual(add_res.status_code, 200)
        self.assertTrue(add_res.json()["ok"])

        # Check User C updated wallet balance (was ₹30, now ₹530)
        bal_res = self.client.get("/api/wallet/balance", headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(bal_res.json()["balance_paise"], 53000)
        self.assertEqual(bal_res.json()["balance_inr"], 530.0)

        # 3. Connect a new channel for User C
        conn_res = self.client.post("/api/channels/connect", json={
            "channel_link": "https://t.me/channel_to_delete",
            "category": "General"
        }, headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(conn_res.status_code, 200)

        # 4. User Deletes Connected Channel
        del_user_res = self.client.post("/api/channels/delete", json={
            "channel_id": "https://t.me/channel_to_delete"
        }, headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(del_user_res.status_code, 200)
        self.assertTrue(del_user_res.json()["ok"])

        # Verify channel no longer in user's list
        chans_after = self.client.get("/api/channels/my", headers={"Authorization": f"Bearer {token_c}"}).json()["channels"]
        has_deleted = any(c["channel_id"] == "https://t.me/channel_to_delete" for c in chans_after)
        self.assertFalse(has_deleted)

        # 5. Connect channel for Admin deletion test
        conn_adm = self.client.post("/api/channels/connect", json={
            "channel_link": "https://t.me/channel_adm_delete",
            "category": "Gaming & eSports"
        }, headers={"Authorization": f"Bearer {token_c}"})
        self.assertEqual(conn_adm.status_code, 200)

        # Admin Deletes Channel
        del_adm_res = self.client.post("/api/admin/channels/delete", json={
            "channel_id": "https://t.me/channel_adm_delete"
        }, headers={"X-Admin-Token": admin_token})
        self.assertEqual(del_adm_res.status_code, 200)
        self.assertTrue(del_adm_res.json()["ok"])


if __name__ == "__main__":
    unittest.main()
