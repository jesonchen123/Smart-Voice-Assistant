"""
Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
SPDX-license-identifier: BSD-3-Clause

火山引擎 OpenAPI V4 签名实现 (Volc V4 / HMAC-SHA256)。
参考: https://www.volcengine.com/docs/6369/67269
"""

import datetime
import hashlib
import hmac
from urllib.parse import quote


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm_query(params: dict) -> str:
    if not params:
        return ''
    pairs = []
    for k in sorted(params.keys()):
        v = params[k]
        if isinstance(v, list):
            for item in v:
                pairs.append(
                    f"{quote(str(k), safe='-_.~')}={quote(str(item), safe='-_.~')}"
                )
        else:
            pairs.append(
                f"{quote(str(k), safe='-_.~')}={quote(str(v), safe='-_.~')}"
            )
    return '&'.join(pairs)


def sign_request(
    method: str,
    host: str,
    path: str,
    params: dict,
    headers: dict,
    body: bytes,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    service: str,
):
    """
    对请求进行火山 V4 签名，返回新的 headers 字典（含 Authorization）。
    """
    now = datetime.datetime.utcnow()
    x_date = now.strftime('%Y%m%dT%H%M%SZ')
    short_date = now.strftime('%Y%m%d')

    payload_hash = _sha256_hex(body or b'')

    headers = dict(headers or {})
    headers['Host'] = host
    headers['X-Date'] = x_date
    headers['X-Content-Sha256'] = payload_hash
    if 'Content-Type' not in headers and 'content-type' not in headers:
        headers['Content-Type'] = 'application/json'

    # 用于签名的 header 集合
    signed_header_keys = ['content-type', 'host', 'x-content-sha256', 'x-date']
    # 构造 canonical headers
    lower_headers = {k.lower(): v for k, v in headers.items()}
    canonical_headers = ''.join(
        f"{k}:{str(lower_headers[k]).strip()}\n" for k in signed_header_keys
    )
    signed_headers = ';'.join(signed_header_keys)

    canonical_query = _norm_query(params)

    canonical_request = '\n'.join([
        method.upper(),
        path or '/',
        canonical_query,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = '\n'.join([
        'HMAC-SHA256',
        x_date,
        credential_scope,
        _sha256_hex(canonical_request.encode('utf-8')),
    ])

    k_date = _hmac_sha256(secret_access_key.encode('utf-8'), short_date)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, 'request')

    signature = hmac.new(
        k_signing, string_to_sign.encode('utf-8'), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers['Authorization'] = authorization
    return headers
