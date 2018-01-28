import asyncio
import aiohttp

import urllib.parse as urlparse
from typing import List

import json

import requests
from steampy import guard
from steampy.chat import SteamChat
from steampy.confirmation import ConfirmationExecutor
from steampy.exceptions import SevenDaysHoldException, LoginRequired, ApiException
from steampy.login import LoginExecutor, InvalidCredentials
from steampy.market import SteamMarket
from steampy.models import Asset, TradeOfferState, SteamUrl, GameOptions
from steampy.utils import text_between, texts_between, merge_items_with_descriptions_from_inventory, \
    steam_id_to_account_id, merge_items_with_descriptions_from_offers, get_description_key, \
    merge_items_with_descriptions_from_offer, account_id_to_steam_id, get_key_value_from_url


def login_required(func):
    async def func_wrapper(self, *args, **kwargs):
        if not self.was_login_executed:
            raise LoginRequired('Use login method first')
        else:
            return await func(self, *args, **kwargs)

    return func_wrapper


class SteamClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        conn = aiohttp.TCPConnector(verify_ssl=True)
        self._session = aiohttp.ClientSession(connector=conn)
        self.steam_guard = None
        self.was_login_executed = False
        self.username = None
        self.market = SteamMarket(self._session)
        self.chat = SteamChat(self._session)

    def __del__(self):
        try:
            self._session.close()
        except:
            pass

    async def login(
            self, username: str, password: str, steam_guard: str) -> None:
        self.steam_guard = await guard.load_steam_guard(steam_guard)
        self.username = username
        login_executor = LoginExecutor(
            username,
            password,
            self.steam_guard['shared_secret'],
            self._session)
        await login_executor.login()
        self.was_login_executed = True
        # await self.market._set_login_executed(
        #     self.steam_guard,
        #     await self._get_session_id())
        # await self.chat._login()

    @login_required
    async def logout(self) -> None:
        url = LoginExecutor.STORE_URL + '/logout/'
        params = {'sessionid': await self._get_session_id()}
        async with self._session.post(url, params) as response:
            await response.text
        if await self.is_session_alive():
            raise Exception("Logout unsuccessful")
        self.was_login_executed = False
        await self.chat._logout()

    @login_required
    async def is_session_alive(self):
        steam_login = self.username
        async with self._session.get(SteamUrl.COMMUNITY_URL) as response:
            main_page_response_text = await response.text()
        return steam_login.lower() in main_page_response_text.lower()

    async def api_call(
            self,
            request_method: str,
            interface: str,
            api_method: str,
            version: str,
            params: dict = None):
        url = '/'.join([SteamUrl.API_URL, interface, api_method, version])
        if request_method == 'GET':
            async with self._session.get(url, params=params) as response:
                text = await response.text()
        else:
            async with self._session.post(url, data=params) as response:
                text = await response.text()
        if self.is_invalid_api_key(text):
            raise InvalidCredentials('Invalid API key')
        return text

    @staticmethod
    def is_invalid_api_key(text) -> bool:
        msg = ('Access is denied. Retrying will not help. '
               'Please verify your <pre>key=</pre> parameter')
        return msg in text

    @login_required
    async def get_my_inventory(
            self,
            game: GameOptions,
            merge: bool = True) -> dict:
        url = SteamUrl.COMMUNITY_URL + '/my/inventory/json/' + \
              game.app_id + '/' + \
              game.context_id
        async with self._session.get(url) as response:
            response_dict = json.loads(await response.text())
            if merge:
                return await merge_items_with_descriptions_from_inventory(
                    response_dict, game)
            return response_dict

    @login_required
    async def get_partner_inventory(
            self,
            partner_steam_id: str,
            game: GameOptions,
            merge: bool = True) -> dict:
        params = {'sessionid': await self._get_session_id(),
                  'partner': partner_steam_id,
                  'appid': int(game.app_id),
                  'contextid': game.context_id}
        print('----params-----')
        print(params)
        partner_account_id = steam_id_to_account_id(partner_steam_id)

        ref_url = '{}/tradeoffer/new/?partner={}'.format(
            SteamUrl.COMMUNITY_URL, partner_account_id)
        headers = {'X-Requested-With': 'XMLHttpRequest',
                   'Referer': ref_url,
                   'X-Prototype-Version': '1.7'}

        url = SteamUrl.COMMUNITY_URL + '/tradeoffer/new/partnerinventory/'
        async with self._session.get(
                url, params=params, headers=headers) as response:
            response_dict = json.loads(await response.text())
            if merge:
                return await merge_items_with_descriptions_from_inventory(
                    response_dict, game)
            return response_dict

    async def _get_session_id(self) -> str:
        for key, val in self._session.cookie_jar._cookies.items():
            if 'sessionid' in val.keys():
                return val['sessionid'].value

    async def get_trade_offers_summary(self) -> dict:
        params = {'key': self._api_key}
        return json.loads(await self.api_call(
            'GET', 'CEconService', 'GetTradeOffersSummary', 'v1', params))

    async def get_trade_offers(self, merge: bool = True):
        params = {'key': self._api_key,
                  'get_sent_offers': 1,
                  'get_received_offers': 1,
                  'get_descriptions': 1,
                  'language': 'english',
                  'active_only': 1,
                  'historical_only': 0,
                  'time_historical_cutoff': ''}
        response = json.loads(await self.api_call(
            'GET', 'CEconService', 'GetTradeOffers', 'v1', params))
        response = await self._filter_non_active_offers(response)
        if merge:
            response = await merge_items_with_descriptions_from_offers(response)
        return response

    @staticmethod
    async def _filter_non_active_offers(offers_response):
        offers_received = offers_response['response'].get('trade_offers_received', [])
        offers_sent = offers_response['response'].get('trade_offers_sent', [])
        offers_response['response']['trade_offers_received'] = list(
            filter(lambda offer: offer['trade_offer_state'] == TradeOfferState.Active, offers_received))
        offers_response['response']['trade_offers_sent'] = list(
            filter(lambda offer: offer['trade_offer_state'] == TradeOfferState.Active, offers_sent))
        return offers_response

    async def get_trade_offer(self, trade_offer_id: str, merge: bool = True) -> dict:
        params = {'key': self._api_key,
                  'tradeofferid': trade_offer_id,
                  'language': 'english'}
        response = json.loads(
            await self.api_call(
                'GET', 'CEconService', 'GetTradeOffer', 'v1', params))
        if merge and "descriptions" in response['response']:
            descriptions = {
                await get_description_key(offer): offer
                for offer in response['response']['descriptions']}
            offer = response['response']['offer']
            response['response']['offer'] = await merge_items_with_descriptions_from_offer(offer, descriptions)
        return response

    def get_trade_history(self,
                          max_trades=100,
                          start_after_time=None,
                          start_after_tradeid=None,
                          get_descriptions=True,
                          navigating_back=True,
                          include_failed=True,
                          include_total=True) -> dict:
        params = {
            'key': self._api_key,
            'max_trades': max_trades,
            'start_after_time': start_after_time,
            'start_after_tradeid': start_after_tradeid,
            'get_descriptions': get_descriptions,
            'navigating_back': navigating_back,
            'include_failed': include_failed,
            'include_total': include_total
        }
        response = json.loads(
            self.api_call(
                'GET', 'IEconService', 'GetTradeHistory', 'v1', params))
        return response

    async def get_trade_receipt(self, trade_id: str) -> list:
        url = "https://steamcommunity.com/trade/{}/receipt".format(trade_id)
        async with self._session.get(url) as response:
            html = await response.text()
            items = []
            for item in texts_between(html, "oItem = ", ";\r\n\toItem"):
                items.append(json.loads(item))
            return items

    @login_required
    async def accept_trade_offer(self, trade_offer_id: str) -> dict:
        trade = await self.get_trade_offer(trade_offer_id)
        trade_offer_state = TradeOfferState(
            trade['response']['offer']['trade_offer_state'])
        if trade_offer_state is not TradeOfferState.Active:
            raise ApiException("Invalid trade offer state: {} ({})".format(trade_offer_state.name,
                                                                           trade_offer_state.value))
        partner = await self._fetch_trade_partner_id(trade_offer_id)
        session_id = await self._get_session_id()
        accept_url = '{}/tradeoffer/{}/accept'.format(
            SteamUrl.COMMUNITY_URL, trade_offer_id)
        params = {'sessionid': session_id,
                  'tradeofferid': trade_offer_id,
                  'serverid': '1',
                  'partner': partner,
                  'captcha': ''}
        headers = {'Referer': await self._get_trade_offer_url(trade_offer_id)}
        async with  self._session.post(
                accept_url, data=params, headers=headers) as response:
            data = json.loads(await response.text())
            if data.get('needs_mobile_confirmation', False):
                return await self._confirm_transaction(trade_offer_id)
            return data

    async def _fetch_trade_partner_id(self, trade_offer_id: str) -> str:
        url = await self._get_trade_offer_url(trade_offer_id)
        async with self._session.get(url) as response:
            offer_response_text = await response.text()
            if 'You have logged in from a new device. In order to protect the items' in offer_response_text:
                raise SevenDaysHoldException("Account has logged in a new device and can't trade for 7 days")
            return text_between(offer_response_text, "var g_ulTradePartnerSteamID = '", "';")

    async def _confirm_transaction(self, trade_offer_id: str) -> dict:
        confirmation_executor = ConfirmationExecutor(
            self.steam_guard['identity_secret'],
            self.steam_guard['steamid'],
            self._session)
        return await confirmation_executor.send_trade_allow_request(trade_offer_id)

    async def decline_trade_offer(self, trade_offer_id: str) -> dict:
        params = {'key': self._api_key,
                  'tradeofferid': trade_offer_id}
        return json.loads(await self.api_call(
            'POST', 'IEconService', 'DeclineTradeOffer', 'v1', params))

    async def cancel_trade_offer(self, trade_offer_id: str) -> dict:
        params = {'key': self._api_key,
                  'tradeofferid': trade_offer_id}
        return json.loads(await self.api_call(
            'POST', 'IEconService', 'CancelTradeOffer', 'v1', params))

    @login_required
    async def make_offer(self, items_from_me: List[Asset], items_from_them: List[Asset], partner_steam_id: str,
                   message: str = '') -> dict:
        offer = await self._create_offer_dict(items_from_me, items_from_them)
        session_id = await self._get_session_id()
        url = SteamUrl.COMMUNITY_URL + '/tradeoffer/new/send'
        server_id = 1
        params = {
            'sessionid': session_id,
            'serverid': server_id,
            'partner': partner_steam_id,
            'tradeoffermessage': message,
            'json_tradeoffer': json.dumps(offer),
            'captcha': '',
            'trade_offer_create_params': '{}'
        }
        partner_account_id = steam_id_to_account_id(partner_steam_id)
        headers = {'Referer': SteamUrl.COMMUNITY_URL + '/tradeoffer/new/?partner=' + partner_account_id,
                   'Origin': SteamUrl.COMMUNITY_URL}
        async with self._session.post(url, data=params, headers=headers) as response:
            data = json.loads(await response.text())
            if data.get('needs_mobile_confirmation'):
                data.update(await self._confirm_transaction(data['tradeofferid']))
            return data

    async def get_profile(self, steam_id: str) -> dict:
        params = {'steamids': steam_id, 'key': self._api_key}
        response = await self.api_call('GET', 'ISteamUser', 'GetPlayerSummaries', 'v0002', params)
        data = json.loads(response)
        return data['response']['players'][0]

    @staticmethod
    async def _create_offer_dict(items_from_me: List[Asset], items_from_them: List[Asset]) -> dict:
        return {
            'newversion': True,
            'version': 4,
            'me': {
                'assets': [asset.to_dict() for asset in items_from_me],
                'currency': [],
                'ready': False
            },
            'them': {
                'assets': [asset.to_dict() for asset in items_from_them],
                'currency': [],
                'ready': False
            }
        }

    @login_required
    async def get_escrow_duration(self, trade_offer_url: str) -> int:
        referer = (SteamUrl.COMMUNITY_URL +
                   urlparse.urlparse(trade_offer_url).path)
        headers = {
            'Referer': referer,
            'Origin': SteamUrl.COMMUNITY_URL}
        async with self._session.get(trade_offer_url, headers=headers) as response:
            text = await response.text()
            my_escrow_duration = int(text_between(text, "var g_daysMyEscrow = ", ";"))
            their_escrow_duration = int(text_between(text, "var g_daysTheirEscrow = ", ";"))
            return max(my_escrow_duration, their_escrow_duration)

    @login_required
    async def make_offer_with_url(
            self,
            items_from_me: List[Asset],
            items_from_them: List[Asset],
            trade_offer_url: str,
            message: str = '') -> dict:
        token = get_key_value_from_url(trade_offer_url, 'token')
        partner_account_id = get_key_value_from_url(trade_offer_url, 'partner')
        partner_steam_id = account_id_to_steam_id(partner_account_id)
        offer = await self._create_offer_dict(items_from_me, items_from_them)
        session_id = await self._get_session_id()
        url = SteamUrl.COMMUNITY_URL + '/tradeoffer/new/send'
        server_id = 1
        trade_offer_create_params = {'trade_offer_access_token': token}
        params = {
            'sessionid': session_id,
            'serverid': server_id,
            'partner': partner_steam_id,
            'tradeoffermessage': message,
            'json_tradeoffer': json.dumps(offer),
            'captcha': '',
            'trade_offer_create_params': json.dumps(trade_offer_create_params)
        }
        ref = SteamUrl.COMMUNITY_URL + urlparse.urlparse(trade_offer_url).path
        headers = {
            'Referer': ref,
            'Origin': SteamUrl.COMMUNITY_URL}
        async with self._session.post(url, data=params, headers=headers) as response:
            data = json.loads(await response.text())
            if data.get('needs_mobile_confirmation'):
                data.update(
                    await self._confirm_transaction(data['tradeofferid']))
            return data

    @staticmethod
    async def _get_trade_offer_url(trade_offer_id: str) -> str:
        return SteamUrl.COMMUNITY_URL + '/tradeoffer/' + trade_offer_id

    @login_required
    async def is_trade_link_correct(self, trade_link=None, steam_id=None):
        headers = {
            'Referer': SteamUrl.COMMUNITY_URL + urlparse.urlparse(trade_link).path,
            'Origin': SteamUrl.COMMUNITY_URL}
        try:
            async with self._session.get(trade_link, headers=headers) as response:
                text = await response.text()
        except Exception as e:
            return {'Error': e.__class__.__name__}

        their_steam_id = str(text_between(text, "var g_ulTradePartnerSteamID = '", "';"))

        result = {'equal': True}
        if steam_id != their_steam_id:
            result = {'equal': False}

        return result
