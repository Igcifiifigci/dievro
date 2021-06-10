"""
Initialisation file - configures logging, enables CORS, and exposes methods for obtaining instances of Redis/GeoIP2.

Copyright::

    +===================================================+
    |                 © 2021 Privex Inc.                |
    |               https://www.privex.io               |
    +===================================================+
    |                                                   |
    |        IP Address Information Tool                |
    |                                                   |
    |        Core Developer(s):                         |
    |                                                   |
    |          (+)  Chris (@someguy123) [Privex]        |
    |                                                   |
    +===================================================+


"""
import socket
import sys
import warnings
# from pathlib import Path
from typing import Any, ContextManager, Iterable, List, Mapping, Type, Union

import geoip2.database
import yaml
from flask import Flask, Request, request
from privex.loghelper import LogHelper
from privex.helpers import DictDataClass, DictObject, Dictable, K, T, ip_is_v6, ip_is_v4, empty, empty_if
from privex.helpers.geoip import geoip_manager

import accept_types
from myip import settings
from myip.settings import RichHandler
from enum import Enum
from flask_cors import CORS
import logging

log = logging.getLogger(__name__)


try:
    from rich.console import Console
    console_std = Console(stderr=False)
    console_err = Console(stderr=True)
    err_print = printerr = print_err = console_err.print
    std_print = printstd = print_std = console_std.print
except Exception as rxe:
    warnings.warn(f"Failed to import rich.logging.Console. Reason: {type(rxe)} - {rxe!s}", ImportWarning)
    Console = None
    from privex.helpers import Mocker, empty, empty_if, ip_is_v6

    console_std = console_err = Mocker.make_mock_class('Console')
    err_print = printerr = print_err = lambda *args, file=sys.stderr, **kwargs: print(*args, file=file, **kwargs)
    std_print = printstd = print_std = print
import redis
import logging


# BASE_DIR = dirname(abspath(__file__))
app = Flask(__name__)
CORS(app)
cf = app.config

app.url_map.strict_slashes = False

for k, v in settings.cf.items():
    cf[k] = v

# if empty(LOG_LEVEL):
#     LOG_LEVEL = logging.DEBUG if DEBUG else logging.INFO

lh = LogHelper('myip')
if settings.USE_RICH_LOGGING:
    lh.get_logger().addHandler(
        RichHandler(level=settings.LOG_LEVEL, console=console_err, rich_tracebacks=settings.RICH_TRACEBACKS)
    )
else:
    lh.add_console_handler(level=settings.LOG_LEVEL, stream=sys.stderr)

lh.add_timed_file_handler(settings.DBG_LOG, when='D', interval=1, backups=14, level=settings.LOG_LEVEL)
lh.add_timed_file_handler(settings.ERR_LOG, when='D', interval=1, backups=14, level=logging.WARNING)

log = lh.get_logger()

#######################################
#
# Functions to initialise various
# connections
#
#######################################

_STORE = {}
"""This dictionary stores instances of various connection classes, such as Redis and GeoIP2"""


def get_redis() -> redis.Redis:
    """Initialise or obtain a Redis instance from _STORE"""
    if 'redis' not in _STORE:
        _STORE['redis'] = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    return _STORE['redis']


class GeoType(Enum):
    # ASN = f'{GEOIP_PREFIX}ASN.mmdb'
    # COUNTRY = f'{GEOIP_PREFIX}Country.mmdb'
    # CITY = f'{GEOIP_PREFIX}City.mmdb'
    ASN = 'asn'
    COUNTRY = 'country'
    CITY = 'city'


def get_geoip(gtype: GeoType = GeoType.CITY) -> ContextManager[geoip2.database.Reader]:
    """
    Initialise or obtain a GeoIP2 Reader instance from _STORE
    
    :param GeoType gtype: The type of GeoIP2 database to load (ASN/City/Country), e.g. ``GeoType.ASN``
    :return geoip2.database.Reader G2Reader: An instance of :class:`geoip2.database.Reader` for looking up IPv4/v6 addresses.

    """
    gtype = str(gtype.value)
    
    # gtype = str(gtype.value)
    
    # l = f'geoip_{gtype}'
    # if l not in _STORE:
    # _STORE[l] = geoip_manager(join(GEOIP_PATH, gtype))
    return geoip_manager(gtype)
    # return _STORE[l]


def get_ip() -> str:
    """Return the user's IP from either the IP header (if USE_IP_HEADER is enabled), or remote_addr"""
    
    h = request.headers
    use_header, iphdr = cf['USE_IP_HEADER'], cf['IP_HEADER']

    if use_header:
        ip = h.get(iphdr, 'Unknown IP...')
        ip = ip.split(',')[0] if ',' in ip else ip
        if settings.USE_FAKE_IPS:
            if ip_is_v6(ip): return settings.FAKE_V6
            return settings.FAKE_V4
        return ip
    if settings.USE_FAKE_IPS:
        if ip_is_v6(request.remote_addr): return settings.FAKE_V6
        return settings.FAKE_V4
    return request.remote_addr or 'Unknown IP...'


def get_rdns(ip):
    try:
        return str(socket.gethostbyaddr(ip)[0])
    except Exception as e:
        log.info('Could not resolve IP %s due to exception %s %s', ip, type(e), str(e))
        return ""


CONTENT_TYPES = dict(
    json=['json', 'application/json', 'application/x-json', 'application/js', 'js', 'api', 'text/json', 'text/x-json'],
    text=['flat', 'txt', 'plain', 'x-plain', 'text', 'text/*', 'text/plain', 'text/x-plain', 'text/plaintext',
          'plaintext', 'plain-text', 'text/plain-text', 'application/plain', 'application/x-plain'
          'application/plaintext', 'application/plain-text' 'application/text'],
    html=['htm', 'html', 'text/html', 'text/x-html', 'text/xhtml', 'text/xhtml+xml', 'application/html',
          'application/x-html', 'application/xhtml', 'application/xhtml+xml',
          'web', 'page', 'webpage'],
    yaml=['yml', 'yaml', 'x-yml', 'x-yaml', 'text/yaml', 'text/vnd.yaml', 'text/yml', 'application/yaml', 'application/yml',
          'text/x-yaml', 'text/x-yml', 'application/vnd.yaml', 'application/x-yaml', 'application/x-yml']
    
)


def json_frm(force=True, silent=True, cache=True, fallback: K = dict, req: Request = None, call_fb=True) -> Union[dict, list, K]:
    """Wrapper around :meth:`.Request.get_json` to allow fallback to ``{}`` or ``[]`` instead of ``None``"""
    req = request if empty(req) else req
    j = req.get_json(force=force, silent=silent, cache=cache)
    if empty(j, itr=True, zero=True):
        if fallback == dict: return {}
        if fallback == list: return []
        if fallback == tuple: return ()
        return fallback() if call_fb and callable(fallback) else fallback
    return j


def merge_frm(use_get=True, use_post=True, use_json=True, req: Request = None, base_obj: Type[T] = DictObject) -> T:
    """Merge GET (args), POST (form), and JSON ( :func:`.json_frm` ) into a singular :class:`.dict`"""
    req = request if empty(req) else req
    data = base_obj()
    if use_get: data = {**data, **dict(req.args)}
    if use_post: data = {**data, **dict(req.form)}
    if use_json: data = {**data, **json_frm(force=True, silent=True, fallback=dict, call_fb=True)}
    return base_obj(data)


def dump_yaml(data: Union[list, tuple, set, dict, Any], **kwargs):
    def _fix_dictobj(d: Any):
        if isinstance(d, (DictObject, DictDataClass, Dictable)):
            d = dict(d)
        if isinstance(d, dict):
            return {kx: _fix_dictobj(vx) for kx, vx in d.items()}
        return d
    
    if isinstance(data, DictObject) or not isinstance(data, (list, tuple, dict, set)):
        try:
            data = dict(data)
        except Exception:
            data = list(data)
    if isinstance(data, (set, tuple)): data = [_fix_dictobj(dv) for dv in list(data)]
    if isinstance(data, dict): data = {dk: _fix_dictobj(dv) for dk, dv in data.items()}
    return yaml.dump(data, **kwargs)


def wants_type(accepts: str = None, headers: Mapping[str, str] = None, query: Mapping[str, str] = None, fmt: str = None) -> str:
    """Returns True if we should be returning a JSON response instead of HTML"""
    headers: Mapping[str, str] = request.headers if empty(headers, itr=True) else headers
    accepts: str = empty_if(accepts, headers.get('Accept', '*/*'))
    accepts: List[accept_types.AcceptableType] = accept_types.parse_header(accepts)
    query = merge_frm() if empty(query) else query
    # Manual override with ?format=json
    fmt = query.get('format', 'html') if empty(fmt) else fmt
    for tname, tlist in CONTENT_TYPES.items():
        if fmt.lower() in tlist: return tname
    if fmt.lower() in ['json', 'application/json', 'js', 'api']: return 'json'
    if fmt.lower() in ['flat', 'plain', 'text', 'text/plain', 'plaintext']: return 'text'
    if fmt.lower() in ['html', 'text/html', 'application/html', 'web', 'page', 'webpage']: return 'html'
    if fmt.lower() in ['yml', 'yaml', 'text/yaml', 'text/yml', 'application/yaml', 'application/yml']: return 'yaml'
    
    # mime = request.accept_mimetypes
    for mt in accepts:
        # If a HTML mimetype is higher than JSON, then they probably don't want JSON.
        if 'html' in mt.mime_type: return 'html'
        for tname, tlist in CONTENT_TYPES.items():
            if mt.mime_type.lower() in tlist: return tname
        # if mt.lower() == 'application/json': return True
    # If in doubt, they don't want JSON.
    return 'any'


def want_json(accepts: str = None, headers: Mapping[str, str] = None, query: Mapping[str, str] = None, **kwargs) -> bool:
    """Returns True if we should be returning a JSON response instead of HTML"""
    if cf['API_ONLY']: return True
    return wants_type(accepts=accepts, headers=headers, query=query, **kwargs) == 'json'


