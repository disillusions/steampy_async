import asyncio
import aiohttp

import json

from decimal import Decimal
from requests import Session
from steampy.confirmation import ConfirmationExecutor
from steampy.exceptions import ApiException, TooManyRequests, LoginRequired
from steampy.models import Currency, SteamUrl, GameOptions
from steampy.utils import text_between, get_listing_id_to_assets_address_from_html, get_market_listings_from_html, \
    merge_items_with_descriptions_from_listing, get_market_sell_listings_from_api


def login_required(func):
    async def func_wrapper(self, *args, **kwargs):
        if not self.was_login_executed:
            raise LoginRequired('Use login method first on SteamClient')
        else:
            return await func(self, *args, **kwargs)

    return func_wrapper


class SteamMarket:

    def __init__(self, session):
        self._session = session
        self._steam_guard = None
        self._session_id = None
        self.was_login_executed = False

    async def _set_login_executed(self, steamguard: dict, session_id: str):
        self._steam_guard = steamguard
        self._session_id = session_id
        self.was_login_executed = True

    async def fetch_price(self, item_hash_name: str, game: GameOptions, currency: str = Currency.USD) -> dict:
        url = SteamUrl.COMMUNITY_URL + '/market/priceoverview/'
        params = {'country': 'PL',
                  'currency': currency,
                  'appid': game.app_id,
                  'market_hash_name': item_hash_name}
        async with self._session.get(url, params=params) as response:
            if response.status_code == 429:
                raise TooManyRequests("You can fetch maximum 20 prices in 60s period")
            return json.loads(await response.text())

    @login_required
    async def get_my_market_listings(self) -> dict:
        async with self._session.get("%s/market" % SteamUrl.COMMUNITY_URL) as response:
            if response.status_code != 200:
                raise ApiException("There was a problem getting the listings. http code: %s" % response.status_code)
            text = await response.text()
            assets_descriptions = json.loads(text_between(text, "var g_rgAssets = ", ";\r\n"))
            listing_id_to_assets_address = get_listing_id_to_assets_address_from_html(text)
            listings = get_market_listings_from_html(text)
            listings = merge_items_with_descriptions_from_listing(listings, listing_id_to_assets_address,
                                                                  assets_descriptions)
            if '<span id="tabContentsMyActiveMarketListings_end">' in text:
                n_showing = int(text_between(text, '<span id="tabContentsMyActiveMarketListings_end">', '</span>'))
                n_total = int(text_between(text, '<span id="tabContentsMyActiveMarketListings_total">', '</span>'))
                if n_total > n_showing:
                    url = "%s/market/mylistings/render/?query=&start=%s&count=%s" % (SteamUrl.COMMUNITY_URL, n_showing, -1)
                    async with self._session.get(url) as response2:
                        if response2.status_code != 200:
                            raise ApiException("There was a problem getting the listings. http code: %s" % response.status_code)
                        jresp = json.loads(await response.text())
                        listing_id_to_assets_address = get_listing_id_to_assets_address_from_html(jresp.get("hovers"))
                        listings_2 = get_market_sell_listings_from_api(jresp.get("results_html"))
                        listings_2 = merge_items_with_descriptions_from_listing(listings_2, listing_id_to_assets_address,
                                                                                jresp.get("assets"))
                        listings["sell_listings"] = {**listings["sell_listings"], **listings_2["sell_listings"]}
                return listings

    @login_required
    async def create_sell_order(self, assetid: str, game: GameOptions, money_to_receive: str) -> dict:
        data = {
            "assetid": assetid,
            "sessionid": self._session_id,
            "contextid": game.context_id,
            "appid": game.app_id,
            "amount": 1,
            "price": money_to_receive
        }
        headers = {'Referer': "%s/profiles/%s/inventory" % (SteamUrl.COMMUNITY_URL, self._steam_guard['steamid'])}
        async with self._session.post(
                SteamUrl.COMMUNITY_URL + "/market/sellitem/", data, headers=headers) as response:
            resp_data = json.loads(await response.text())
            if resp_data.get("needs_mobile_confirmation"):
                return await self._confirm_sell_listing(assetid)
            return resp_data

    @login_required
    async def create_buy_order(self, market_name: str, price_single_item: str, quantity: int, game: GameOptions,
                               currency: Currency = Currency.USD) -> dict:
        data = {
            "sessionid": self._session_id,
            "currency": currency.value,
            "appid": game.app_id,
            "market_hash_name": market_name,
            "price_total": str(Decimal(price_single_item) * Decimal(quantity)),
            "quantity": quantity
        }
        headers = {'Referer': "%s/market/listings/%s/%s" % (SteamUrl.COMMUNITY_URL, game.app_id, market_name)}
        async with self._session.post(
                SteamUrl.COMMUNITY_URL + "/market/createbuyorder/", data, headers=headers) as response:
            resp_data = json.loads(await response.text())
            if resp_data.get("success") != 1:
                raise ApiException("There was a problem creating the order. Are you using the right currency? success: %s"
                                   % resp_data.get("success"))
            return resp_data

    @login_required
    async def cancel_sell_order(self, sell_listing_id: str) -> None:
        data = {"sessionid": self._session_id}
        headers = {'Referer': SteamUrl.COMMUNITY_URL + "/market/"}
        url = "%s/market/removelisting/%s" % (SteamUrl.COMMUNITY_URL, sell_listing_id)
        async with self._session.post(url, data=data, headers=headers) as response:
            await response.read()
            if response.status_code != 200:
                raise ApiException("There was a problem removing the listing. http code: %s" % response.status_code)

    @login_required
    async def cancel_buy_order(self, buy_order_id) -> dict:
        data = {
            "sessionid": self._session_id,
            "buy_orderid": buy_order_id
        }
        headers = {"Referer": SteamUrl.COMMUNITY_URL + "/market"}
        async with self._session.post(
                SteamUrl.COMMUNITY_URL + "/market/cancelbuyorder/",
                data,
                headers=headers) as response:
            resp_data = json.loads(await response.text())
            if resp_data.get("success") != 1:
                raise ApiException(
                    "There was a problem canceling the order. success: %s" % resp_data.get("success"))
            return resp_data

    async def _confirm_sell_listing(self, asset_id: str) -> dict:
        con_executor = ConfirmationExecutor(
            self._steam_guard['identity_secret'],
            self._steam_guard['steamid'],
            self._session)
        return await con_executor.confirm_sell_listing(asset_id)
