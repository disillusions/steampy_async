import asyncio
import aiohttp

from yarl import URL

import base64
import time
import requests
import json
from steampy import guard
import rsa

from steampy.exceptions import InvalidCredentials, CaptchaRequired


class LoginExecutor:
    COMMUNITY_URL = "https://steamcommunity.com"
    STORE_URL = 'https://store.steampowered.com'

    def __init__(self, username: str, password: str, shared_secret: str, session) -> None:
        self.username = username
        self.password = password
        self.one_time_code = ''
        self.shared_secret = shared_secret
        self.session = session

    async def login(self):
        login_response = await self._send_login_request()
        print('\nLOGIN RESPONSE\n')
        await self._check_for_captcha(login_response)
        login_response = await self._enter_steam_guard_if_necessary(login_response)
        await self._assert_valid_credentials(login_response)
        await self._perform_redirects(json.loads(await login_response.text()))
        await self.set_sessionid_cookies(login_response)
        return self.session

    async def _send_login_request(self):
        print('FETCHING RSA PARAMS')
        rsa_params = await self._fetch_rsa_params()
        print('DONE FETCHING')
        print('!!!!!!!!!!!!!!=========+!!!!!!!!!!!!!!!!!!')
        print(rsa_params)
        encrypted_password = await self._encrypt_password(rsa_params)
        print('!!!!!!!!!!!!!!=========+!!!!!!!!!!!!!!!!!!')
        print(encrypted_password)
        rsa_timestamp = rsa_params['rsa_timestamp']
        print('!!!!!!!!!!!!!!=========+!!!!!!!!!!!!!!!!!!')
        (print(rsa_timestamp))
        request_data = await self._prepare_login_request_data(encrypted_password, rsa_timestamp)
        print(request_data)
        async with self.session.post(
                self.STORE_URL + '/login/dologin/', data=request_data) as response:
            await response.text()
            print(response.status)
            print(response.headers)
            return response

    async def set_sessionid_cookies(self, login_request=None):
        sessionid = self.session.cookie_jar._cookies['help.steampowered.com']['sessionid'].value

        community_domain = self.COMMUNITY_URL[8:]
        store_domain = self.STORE_URL[8:]
        self.session.cookie_jar.update_cookies(
            {'sessionid': sessionid}, response_url=URL(community_domain))
        self.session.cookie_jar.update_cookies(
            {'sessionid': sessionid}, response_url=URL(store_domain))

    async def _fetch_rsa_params(self, current_number_of_repetitions: int = 0) -> dict:
        maximal_number_of_repetitions = 5
        async with self.session.post(
                self.STORE_URL + '/login/getrsakey/',
                data={'username': self.username}) as response:
            print('IN')
            key_response = json.loads(await response.text())
            print('KEY RESPONSE')
            try:
                rsa_mod = int(key_response['publickey_mod'], 16)
                rsa_exp = int(key_response['publickey_exp'], 16)
                rsa_timestamp = key_response['timestamp']
                return {'rsa_key': rsa.PublicKey(rsa_mod, rsa_exp),
                        'rsa_timestamp': rsa_timestamp}
            except KeyError:
                if current_number_of_repetitions < maximal_number_of_repetitions:
                    return asyncio.ensure_future(self._fetch_rsa_params(current_number_of_repetitions + 1))
                else:
                    raise ValueError('Could not obtain rsa-key')

    async def _encrypt_password(self, rsa_params: dict) -> str:
        return base64.b64encode(rsa.encrypt(self.password.encode('utf-8'), rsa_params['rsa_key']))

    async def _prepare_login_request_data(self, encrypted_password: str, rsa_timestamp: str) -> dict:
        return {
            'password': encrypted_password.decode('utf-8'),
            'username': self.username,
            'twofactorcode': self.one_time_code,
            'emailauth': '',
            'loginfriendlyname': '',
            'captchagid': '-1',
            'captcha_text': '',
            'emailsteamid': '',
            'rsatimestamp': rsa_timestamp,
            'remember_login': 'false',
            'donotcache': str(int(time.time() * 1000))
        }

    @staticmethod
    async def _check_for_captcha(login_response) -> None:
        if json.loads(await login_response.text()).get('captcha_needed', False):
            raise CaptchaRequired('Captcha required')

    async def _enter_steam_guard_if_necessary(self, login_response):
        data = json.loads(await login_response.text())
        print('response data in steam guard')
        print(data)
        if data.get('requires_twofactor'):
            self.one_time_code = await guard.generate_one_time_code(self.shared_secret)
            return await self._send_login_request()
        return login_response

    @staticmethod
    async def _assert_valid_credentials(login_response):
        data = json.loads(await login_response.text())
        if not data['success']:
            raise InvalidCredentials(str(data))

    async def _perform_redirects(self, response_dict: dict) -> None:
        print(response_dict)
        print('^'*6)
        parameters = response_dict.get('transfer_parameters')
        if parameters is None:
            raise Exception('Cannot perform redirects after login, no parameters fetched')
        for url in response_dict['transfer_urls']:
            async with self.session.post(url, data=parameters) as response:
                await response.read()

    async def _fetch_home_page(self, session: requests.Session) -> requests.Response:
        async with session.post(self.COMMUNITY_URL + '/my/home/') as response:
            return await response.text()
