import asyncio
import pytest

from unittest import TestCase

from steampy.client import SteamClient
from steampy.exceptions import LoginRequired, TooManyRequests
from steampy.models import GameOptions, Asset, Currency
from steampy.utils import account_id_to_steam_id


class Credentials:
    login = 'monsterwheel01'
    password = '3df7bpgzPS'
    api_key = '5C46191EC0B67A55AD574881D5C99EF4'


class TestSteamClient:

    credentials = Credentials()
    steam_guard_file = '''{
        "steam_id": "76561197991861652",
        "shared_secret": "zkDhn61kj8WD1PDlopfEIqR9vWY=",
        "identity_secret": "uaNLaqNO8PPK6phxwbkmBk4uSDk="}'''
    @pytest.mark.asyncio
    async def test_login(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)

    @pytest.mark.asyncio
    async def test_is_session_alive(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        self.assertTrue(await client.is_session_alive())

    @pytest.mark.asyncio
    async def test_logout(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        self.assertTrue(await client.is_session_alive())
        await client.logout()

    @pytest.mark.asyncio
    async def test_send_offer_without_sessionid_cookie(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        client._session.cookie_jar.update_cookies(
            {'sessionid': None},
            response_url="steamcommunity.com")
        cookies = client._session.cookie_jar['steamcommunity.com']
        self.assertFalse('sessionid' in cookies)
        game = GameOptions.TF2
        asset_id = ''
        my_asset = Asset(asset_id, game)
        trade_offer_url = ''
        make_offer = lambda: client.make_offer_with_url([my_asset], [], trade_offer_url, "TEST")
        self.assertRaises(AttributeError, make_offer)

    @pytest.mark.asyncio
    async def test_sessionid_cookie(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        community_cookies = client._session.cookie_jar["steamcommunity.com"]
        store_cookies = client._session.cookie_jar["store.steampowered.com"]
        self.assertTrue("sessionid" in community_cookies)
        self.assertTrue("sessionid" in store_cookies)

    @pytest.mark.asyncio
    async def test_get_my_inventory(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        inventory = await client.get_my_inventory(GameOptions.TF2)
        self.assertIsNotNone(inventory)

    @pytest.mark.asyncio
    async def test_get_partner_inventory(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        partner_id = ''
        game = GameOptions.TF2
        inventory = await client.get_partner_inventory(partner_id, game)
        self.assertIsNotNone(inventory)

    @pytest.mark.asyncio
    async def test_get_trade_offers_summary(self):
        client = SteamClient(self.credentials.api_key)
        summary = await client.get_trade_offers_summary()
        self.assertIsNotNone(summary)

    @pytest.mark.asyncio
    async def test_get_trade_offers(self):
        client = SteamClient(self.credentials.api_key)
        offers = await client.get_trade_offers()
        self.assertIsNotNone(offers)

    @pytest.mark.asyncio
    async def test_get_trade_offer(self):
        client = SteamClient(self.credentials.api_key)
        trade_offer_id = '1442685162'
        offer = await client.get_trade_offer(trade_offer_id)
        self.assertIsNotNone(offer)

    @pytest.mark.asyncio
    async def test_accept_trade_offer_without_login(self):
        client = SteamClient(self.credentials.api_key)
        with self.assertRaises(LoginRequired):
            await client.accept_trade_offer('id')

    @pytest.mark.asyncio
    async def test_accept_trade_offer(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        trade_offer_id = '1451378159'
        response_dict = await client.accept_trade_offer(trade_offer_id)
        self.assertIsNotNone(response_dict)

    @pytest.mark.asyncio
    async def test_decline_trade_offer(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        trade_offer_id = '1449530707'
        response_dict = await client.decline_trade_offer(trade_offer_id)
        self.assertEqual(response_dict['response'], {})

    @pytest.mark.asyncio
    async def test_cancel_trade_offer(self):
        client = SteamClient(self.credentials.api_key)
        client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        trade_offer_id = '1450637835'
        response_dict = client.cancel_trade_offer(trade_offer_id)
        self.assertEqual(response_dict['response'], {})

    @pytest.mark.asyncio
    async def test_get_price(self):
        client = SteamClient(self.credentials.api_key)
        item = 'M4A1-S | Cyrex (Factory New)'
        prices = await client.market.fetch_price(item, GameOptions.CS)
        self.assertTrue(prices['success'])

    @pytest.mark.asyncio
    async def test_get_price_to_many_requests(self):
        async def request_loop() -> None:
            item = 'M4A1-S | Cyrex (Factory New)'
            for _ in range(21):
                await client.market.fetch_price(item, GameOptions.CS)

        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        self.assertRaises(TooManyRequests, await request_loop)

    @pytest.mark.asyncio
    async def test_make_offer(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        partner_id = ''
        game = GameOptions.CS
        my_items = await client.get_my_inventory(game)
        partner_items = await client.get_partner_inventory(partner_id, game)
        my_first_item = next(iter(my_items.values()))
        partner_first_item = next(iter(partner_items.values()))
        my_asset = Asset(my_first_item['id'], game)
        partner_asset = Asset(partner_first_item['id'], game)
        response = await client.make_offer([my_asset], [partner_asset], partner_id, 'TESTOWA OFERTA')
        self.assertIsNotNone(response)
        self.assertIn('tradeofferid', response.keys())

    @pytest.mark.asyncio
    async def test_make_offer_url(self):
        partner_account_id = '32384925'
        partner_token = '7vqRtBpC'
        sample_trade_url = 'https://steamcommunity.com/tradeoffer/new/?partner=' + partner_account_id + '&token=' + partner_token
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        async with client._session.head('http://steamcommunity.com') as response:
            await response.read()
            partner_steam_id = account_id_to_steam_id(partner_account_id)
            game = GameOptions.CS
            my_items = await client.get_my_inventory(game, merge=False)['rgInventory']
            partner_items = await client.get_partner_inventory(partner_steam_id, game, merge=False)['rgInventory']
            my_first_item = next(iter(my_items.values()))
            partner_first_item = next(iter(partner_items.values()))
            my_asset = Asset(my_first_item['id'], game)
            partner_asset = Asset(partner_first_item['id'], game)
            response = await client.make_offer_with_url([my_asset], [partner_asset], sample_trade_url, 'TESTOWA OFERTA')
            self.assertIsNotNone(response)
            self.assertIn('tradeofferid', response.keys())

    @pytest.mark.asyncio
    async def test_get_escrow_duration(self):
        sample_trade_url = "https://steamcommunity.com/tradeoffer/new/?partner=314218906&token=sgA4FdNm"  # a sample trade url with escrow time of 15 days cause mobile auth not added
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        response = await client.get_escrow_duration(sample_trade_url)
        self.assertEqual(response, 15)

    @pytest.mark.asyncio
    async def test_get_all_listings_from_market(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        listings = await client.market.get_my_market_listings()
        self.assertTrue(len(listings) == 2)
        self.assertTrue(len(listings.get("buy_orders")) == 1)
        self.assertTrue(len(listings.get("sell_listings")) == 1)
        self.assertIsInstance(next(iter(listings.get("sell_listings").values())).get("description"), dict)

    @pytest.mark.asyncio
    async def test_create_and_remove_sell_listing(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        game = GameOptions.DOTA2
        inventory = await client.get_my_inventory(game)
        asset_id_to_sell = None
        for asset_id, item in inventory.items():
            if item.get("marketable") == 1:
                asset_id_to_sell = asset_id
                break
        self.assertIsNotNone(asset_id_to_sell, "You need at least 1 marketable item to pass this test")
        response = await client.market.create_sell_order(asset_id_to_sell, game, "10000")
        self.assertTrue(response["success"])
        sell_listings = await client.market.get_my_market_listings()["sell_listings"]
        listing_to_cancel = None
        for listing in sell_listings.values():
            if listing["description"]["id"] == asset_id_to_sell:
                listing_to_cancel = listing["listing_id"]
                break
        self.assertIsNotNone(listing_to_cancel)
        response = await client.market.cancel_sell_order(listing_to_cancel)

    @pytest.mark.asyncio
    async def test_create_and_cancel_buy_order(self):
        client = SteamClient(self.credentials.api_key)
        await client.login(self.credentials.login, self.credentials.password, self.steam_guard_file)
        # PUT THE REAL CURRENCY OF YOUR STEAM WALLET, OTHER CURRENCIES WILL NOT WORK
        response = await client.market.create_buy_order("AK-47 | Redline (Field-Tested)", "10.34", 2, GameOptions.CS, Currency.EURO)
        buy_order_id = response["buy_orderid"]
        self.assertTrue(response["success"] == 1)
        self.assertIsNotNone(buy_order_id)
        response = await client.market.cancel_buy_order(buy_order_id)
        self.assertTrue(response["success"])
