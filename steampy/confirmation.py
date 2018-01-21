import asyncio
import aiohttp

import enum
import json
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from steampy import guard
from steampy.exceptions import ConfirmationExpected
from steampy.login import InvalidCredentials


class Confirmation:
    def __init__(self, _id, data_confid, data_key):
        self.id = _id.split('conf')[1]
        self.data_confid = data_confid
        self.data_key = data_key


class Tag(enum.Enum):
    CONF = 'conf'
    DETAILS = 'details'
    ALLOW = 'allow'
    CANCEL = 'cancel'


class ConfirmationExecutor:
    CONF_URL = "https://steamcommunity.com/mobileconf"

    def __init__(self, identity_secret: str, my_steam_id: str, session: requests.Session) -> None:
        self._my_steam_id = my_steam_id
        self._identity_secret = identity_secret
        self._session = session

    async def send_trade_allow_request(self, trade_offer_id: str) -> dict:
        confirmations = await self._get_confirmations()
        confirmation = await self._select_trade_offer_confirmation(
            confirmations, trade_offer_id)
        return await self._send_confirmation(confirmation)

    async def confirm_sell_listing(self, asset_id: str) -> dict:
        confirmations = await self._get_confirmations()
        confirmation = await self._select_sell_listing_confirmation(confirmations, asset_id)
        return await self._send_confirmation(confirmation)

    async def _send_confirmation(self, confirmation: Confirmation) -> dict:
        tag = Tag.ALLOW
        params = await self._create_confirmation_params(tag.value)
        params['op'] = tag.value,
        params['cid'] = confirmation.data_confid
        params['ck'] = confirmation.data_key
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        async with self._session.get(
            self.CONF_URL + '/ajaxop', params=params, headers=headers) as response:
            return json.loads(await response.text())

    async def _get_confirmations(self) -> List[Confirmation]:
        confirmations = []
        confirmations_page = await self._fetch_confirmations_page()
        soup = BeautifulSoup(confirmations_page.text, 'html.parser')
        if soup.select('#mobileconf_empty'):
            return confirmations
        for confirmation_div in soup.select('#mobileconf_list .mobileconf_list_entry'):
            _id = confirmation_div['id']
            data_confid = confirmation_div['data-confid']
            data_key = confirmation_div['data-key']
            confirmations.append(Confirmation(_id, data_confid, data_key))
        return confirmations

    async def _fetch_confirmations_page(self) -> requests.Response:
        tag = Tag.CONF.value
        params = self._create_confirmation_params(tag)
        headers = {'X-Requested-With': 'com.valvesoftware.android.steam.community'}
        async with self._session.get(self.CONF_URL + '/conf', params=params, headers=headers) as response:
            text = await response.text()
            if 'Steam Guard Mobile Authenticator is providing incorrect Steam Guard codes.' in text:
                raise InvalidCredentials('Invalid Steam Guard file')
            return text

    async def _fetch_confirmation_details_page(self, confirmation: Confirmation) -> str:
        tag = 'details' + confirmation.id
        params = await self._create_confirmation_params(tag)
        async with self._session.get(
            self.CONF_URL + '/details/' + confirmation.id, params=params) as response:
            resp_data = json.loads(await response.text())
            return resp_data['html']

    async def _create_confirmation_params(self, tag_string: str) -> dict:
        timestamp = int(time.time())
        confirmation_key = await guard.generate_confirmation_key(
            self._identity_secret, tag_string, timestamp)
        android_id = await guard.generate_device_id(self._my_steam_id)
        return {'p': android_id,
                'a': self._my_steam_id,
                'k': confirmation_key,
                't': timestamp,
                'm': 'android',
                'tag': tag_string}

    async def _select_trade_offer_confirmation(self, confirmations: List[Confirmation], trade_offer_id: str) -> Confirmation:
        for confirmation in confirmations:
            confirmation_details_page = await self._fetch_confirmation_details_page(confirmation)
            confirmation_id = await self._get_confirmation_trade_offer_id(confirmation_details_page)
            if confirmation_id == trade_offer_id:
                return confirmation
        raise ConfirmationExpected

    async def _select_sell_listing_confirmation(self, confirmations: List[Confirmation], asset_id: str) -> Confirmation:
        for confirmation in confirmations:
            confirmation_details_page = await self._fetch_confirmation_details_page(confirmation)
            confirmation_id = await self._get_confirmation_sell_listing_id(confirmation_details_page)
            if confirmation_id == asset_id:
                return confirmation
        raise ConfirmationExpected

    @staticmethod
    async def _get_confirmation_sell_listing_id(confirmation_details_page: str) -> str:
        soup = BeautifulSoup(confirmation_details_page, 'html.parser')
        scr_raw = soup.select("script")[2].text.strip()
        scr_raw = scr_raw[scr_raw.index("'confiteminfo', ") + 16:]
        scr_raw = scr_raw[:scr_raw.index(", UserYou")].replace("\n", "")
        return json.loads(scr_raw)["id"]

    @staticmethod
    async def _get_confirmation_trade_offer_id(confirmation_details_page: str) -> str:
        soup = BeautifulSoup(confirmation_details_page, 'html.parser')
        full_offer_id = soup.select('.tradeoffer')[0]['id']
        return full_offer_id.split('_')[1]
