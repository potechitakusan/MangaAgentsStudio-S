"""Server-side settings and HTTP transport for the local production AI."""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import json
import os
import re
import urllib.parse
import urllib.request
from . import project as db

DEFAULT_URL = 'http://127.0.0.1:8080/v1/chat/completions'
DEFAULT_MODEL = 'qwen3.8-flash-next-iq2_xs'


@dataclass(frozen=True, repr=False)
class Settings:
    url: str
    model: str

    @property
    def models_url(self):
        return self.url.removesuffix('chat/completions') + 'models'


def _file_values():
    """Read only our two keys; never execute, interpolate or export .env."""
    path = db.ROOT / '.env'
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise db.StudioError('.envをUTF-8で読み込めません') from None
    found = {}
    for line in lines:
        name, sep, value = line.partition('=')
        name = name.strip().removeprefix('export ').strip()
        if not sep or name not in ('LOCAL_AI_URL', 'LOCAL_AI_MODEL'):
            continue
        if name in found:
            raise db.StudioError(name + 'が.env内で重複しています')
        value = value.strip()
        if value.startswith('#'):
            value = ''
        elif value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end+1:].strip() and not value[end+1:].strip().startswith('#')):
                raise db.StudioError(name + 'の引用符を確認してください')
            value = value[1:end]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        found[name] = value
    return found


@lru_cache(maxsize=1)
def settings():
    values = _file_values()
    url = os.environ.get('LOCAL_AI_URL', values.get('LOCAL_AI_URL', '')).strip() or DEFAULT_URL
    model = os.environ.get('LOCAL_AI_MODEL', values.get('LOCAL_AI_MODEL', '')).strip() or DEFAULT_MODEL
    try:
        parts = urllib.parse.urlsplit(url)
        valid = (url.isascii() and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
                 and '\\' not in url and parts.scheme in ('http', 'https') and parts.hostname
                 and (parts.port is None or 1 <= parts.port <= 65535)
                 and parts.username is None and parts.password is None
                 and not parts.query and not parts.fragment
                 and '?' not in url and '#' not in url
                 and parts.path.endswith('/chat/completions'))
    except ValueError:
        valid = False
    if not valid:
        raise db.StudioError('LOCAL_AI_URLは認証情報・クエリなしのhttp(s) URLで、末尾を/chat/completionsにしてください')
    if len(model) > 256 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in model):
        raise db.StudioError('LOCAL_AI_MODELは空白・制御文字なしの256文字以内で指定してください')
    return Settings(url, model)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise db.StudioError('制作AIのリダイレクトを拒否しました。LOCAL_AI_URLを確認してください')


def _open(request, timeout):
    # A configured local/LAN server must not be reached through a system proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def chat(messages, tools, timeout):
    config = settings()
    payload = dict(model=config.model, messages=messages, tools=tools, tool_choice='auto',
                   temperature=.2, max_tokens=8192, stream=False, reasoning_effort='none')
    try:
        request = urllib.request.Request(config.url, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
        with _open(request, timeout) as response:
            data = json.load(response)
        return data['choices'][0]['message'], data.get('usage', {})
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        # Neither endpoint values nor upstream error bodies belong in logs/UI.
        raise db.StudioError('制作AIの応答を取得できません。起動状態と.envのLOCAL_AI_URL・LOCAL_AI_MODELを確認してください') from None


def check():
    """Advisory model-list check; never send fiction or generate tokens."""
    try:
        config = settings()
        request = urllib.request.Request(config.models_url, headers={'Accept': 'application/json'})
        with _open(request, 2) as response:
            data = json.load(response)
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise ValueError()
        if not any(isinstance(row, dict) and row.get('id') == config.model for row in data['data']):
            return dict(connected=False, message='制作AIは応答しましたが、LOCAL_AI_MODELがモデル一覧にありません。設定を確認してください。Studioは起動できます。')
        return dict(connected=True, message='制作AIの接続とモデル名を確認しました。ツール呼出し・生成の動作は未確認です。')
    except db.StudioError as exc:
        return dict(connected=False, message=str(exc) + '。Studioは起動できます。')
    except (OSError, ValueError, TypeError):
        return dict(connected=False, message='制作AIのモデル一覧APIで接続確認できませんでした。起動状態と.envの設定を確認してください。Studioは起動できます。Grokのみ利用する場合は不要です。')
