"""
Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
SPDX-license-identifier: BSD-3-Clause

火山引擎 RTC AccessToken Python 实现，与 Node.js 版本二进制兼容。
"""

import base64
import hmac
import hashlib
import random
import struct
import time

VERSION = "001"
VERSION_LENGTH = 3
APP_ID_LENGTH = 24


class Privileges:
    PrivPublishStream = 0
    privPublishAudioStream = 1
    privPublishVideoStream = 2
    privPublishDataStream = 3
    PrivSubscribeStream = 4


privileges = Privileges


class _ByteBuf:
    def __init__(self):
        self._chunks = []

    def put_uint16(self, v):
        self._chunks.append(struct.pack('<H', v & 0xFFFF))
        return self

    def put_uint32(self, v):
        self._chunks.append(struct.pack('<I', v & 0xFFFFFFFF))
        return self

    def put_bytes(self, data: bytes):
        self.put_uint16(len(data))
        self._chunks.append(data)
        return self

    def put_string(self, s):
        if isinstance(s, str):
            s = s.encode('utf-8')
        return self.put_bytes(s)

    def put_tree_map_uint32(self, m):
        if not m:
            self.put_uint16(0)
            return self
        self.put_uint16(len(m))
        # 与 Node 实现保持一致：按插入顺序写入键
        for k, v in m.items():
            self.put_uint16(int(k))
            self.put_uint32(int(v))
        return self

    def pack(self) -> bytes:
        return b''.join(self._chunks)


def _encode_hmac(key, message: bytes) -> bytes:
    if isinstance(key, str):
        key = key.encode('utf-8')
    return hmac.new(key, message, hashlib.sha256).digest()


class AccessToken:
    def __init__(self, app_id, app_key, room_id, user_id):
        self.app_id = app_id
        self.app_key = app_key
        self.room_id = room_id
        self.user_id = user_id
        self.issued_at = int(time.time())
        self.nonce = random.randint(0, 0xFFFFFFFF)
        self.expire_at = 0
        self.privileges = {}
        self.signature = None

    def add_privilege(self, privilege, expire_timestamp):
        self.privileges[privilege] = expire_timestamp
        if privilege == Privileges.PrivPublishStream:
            self.privileges[Privileges.privPublishVideoStream] = expire_timestamp
            self.privileges[Privileges.privPublishAudioStream] = expire_timestamp
            self.privileges[Privileges.privPublishDataStream] = expire_timestamp

    def expire_time(self, expire_timestamp):
        self.expire_at = expire_timestamp

    def _pack_msg(self) -> bytes:
        buf = _ByteBuf()
        buf.put_uint32(self.nonce)
        buf.put_uint32(self.issued_at)
        buf.put_uint32(self.expire_at)
        buf.put_string(self.room_id)
        buf.put_string(self.user_id)
        buf.put_tree_map_uint32(self.privileges)
        return buf.pack()

    def serialize(self) -> str:
        msg = self._pack_msg()
        signature = _encode_hmac(self.app_key, msg)
        content_buf = _ByteBuf()
        content_buf.put_bytes(msg)
        content_buf.put_bytes(signature)
        content = content_buf.pack()
        return VERSION + self.app_id + base64.b64encode(content).decode('ascii')
