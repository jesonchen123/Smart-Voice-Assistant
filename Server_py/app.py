"""
Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
SPDX-license-identifier: BSD-3-Clause

AIGC Server (FastAPI 版本)
- 与 Node.js 版本接口一致:
    POST /proxy?Action=xxx&Version=xxx   代理火山 RTC OpenAPI
    GET/POST /getScenes                  获取本地 scenes/*.json 场景配置
"""

import copy
import json
import os
import time
import uuid
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from signer import sign_request
from token_manager import AccessToken, Privileges
from util import assert_, read_files, wrapped_response_async

Scenes = read_files('./scenes', '.json')

# LLMServer 内网地址。RTC 看到的是 LLMConfig.Url 里的公网 ngrok 地址,
# 但 Server_py 自己调 /v1/context/create 走本机直连更快也更稳。
LLM_SERVER_INTERNAL_URL = os.environ.get('LLM_SERVER_INTERNAL_URL', 'http://localhost:3001')


async def _create_llm_context() -> str | None:
    """启动通话前调一次 LLMServer 建 session, 失败返回 None (降级不阻塞通话)。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f'{LLM_SERVER_INTERNAL_URL.rstrip("/")}/v1/context/create',
                json={},
            )
        if resp.status_code >= 400:
            print(f'[ctx] /v1/context/create HTTP {resp.status_code}: {resp.text[:200]}')
            return None
        ctx = (resp.json() or {}).get('context_id')
        if not ctx:
            print(f'[ctx] LLMServer 没返回 context_id: {resp.text[:200]}')
            return None
        return ctx
    except Exception as e:
        print(f'[ctx] 创建 context 失败 (降级为无 session): {e}')
        return None


def _inject_context_id_into_url(url: str, context_id: str) -> str:
    """把 context_id 作为 query string 拼到 LLMConfig.Url 上, 已有同名 key 会被覆盖。"""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query['context_id'] = context_id
    return urlunparse(parsed._replace(query=urlencode(query)))


def _sync_rtc_identity(rtc_config: dict, voice_chat: dict, room_id: str, user_id: str):
    rtc_config['RoomId'] = room_id
    rtc_config['UserId'] = user_id
    voice_chat['RoomId'] = room_id

    agent_config = voice_chat.setdefault('AgentConfig', {})
    target_user_ids = agent_config.setdefault('TargetUserId', [user_id])
    if target_user_ids:
        target_user_ids[0] = user_id
    else:
        target_user_ids.append(user_id)


def _generate_rtc_token(app_id: str, app_key: str, room_id: str, user_id: str) -> str:
    key = AccessToken(app_id, app_key, room_id, user_id)
    key.add_privilege(Privileges.PrivSubscribeStream, 0)
    key.add_privilege(Privileges.PrivPublishStream, 0)
    key.expire_time(int(time.time()) + 24 * 3600)
    return key.serialize()

app = FastAPI(title='AIGC Server (Python)', version='1.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.api_route('/proxy', methods=['POST', 'OPTIONS'])
async def proxy(request: Request):
    """代理 AIGC 的 OpenAPI 请求"""
    if request.method == 'OPTIONS':
        return JSONResponse(content=None, status_code=204)

    async def logic():
        action = request.query_params.get('Action')
        version = request.query_params.get('Version', '2024-12-01')
        assert_(action, 'Action 不能为空')
        assert_(version, 'Version 不能为空')

        try:
            body_json = await request.json()
        except Exception:
            body_json = {}
        body_json = body_json or {}

        scene_id = body_json.get('SceneID')
        assert_(scene_id, 'SceneID 不能为空, SceneID 用于指定场景的 JSON')

        scene_data = Scenes.get(scene_id)
        assert_(scene_data, f'{scene_id} 不存在, 请先在 Server_py/scenes 下定义该场景的 JSON.')

        voice_chat = scene_data.get('VoiceChat', {}) or {}
        account_config = scene_data.get('AccountConfig', {}) or {}
        access_key_id = account_config.get('accessKeyId')
        secret_key = account_config.get('secretKey')
        assert_(access_key_id, 'AccountConfig.accessKeyId 不能为空')
        assert_(secret_key, 'AccountConfig.secretKey 不能为空')

        body = {}
        if action == 'StartVoiceChat':
            # 深拷贝避免污染内存中的 Scenes (后续会话还要复用原配置)
            body = copy.deepcopy(voice_chat)
            llm_cfg = (
                body.setdefault('Config', {})
                    .setdefault('LLMConfig', {})
            )
            llm_url = llm_cfg.get('Url')
            if llm_url:
                # 前端如果指定了 ResumeSessionId, 直接用它续聊;
                # 否则调 LLMServer 新建一个 context。
                ctx_id = (body_json.get('ResumeSessionId') or '').strip()
                if ctx_id:
                    print(f'[ctx] 续聊 context_id={ctx_id} (前端指定)')
                else:
                    ctx_id = await _create_llm_context()
                    if ctx_id:
                        print(f'[ctx] 新建 context_id={ctx_id}')
                if ctx_id:
                    llm_cfg['Url'] = _inject_context_id_into_url(llm_url, ctx_id)
                # 拿不到 context_id 就保持原 URL, LLMServer 会走 stateless 分支
        elif action == 'StopVoiceChat':
            app_id = voice_chat.get('AppId')
            room_id = voice_chat.get('RoomId')
            task_id = voice_chat.get('TaskId')
            assert_(app_id, 'VoiceChat.AppId 不能为空')
            assert_(room_id, 'VoiceChat.RoomId 不能为空')
            assert_(task_id, 'VoiceChat.TaskId 不能为空')
            body = {'AppId': app_id, 'RoomId': room_id, 'TaskId': task_id}

        body_bytes = json.dumps(body, ensure_ascii=False).encode('utf-8')
        params = {'Action': action, 'Version': version}
        host = 'rtc.volcengineapi.com'

        signed_headers = sign_request(
            method='POST',
            host=host,
            path='/',
            params=params,
            headers={
                'Host': host,
                'Content-Type': 'application/json',
            },
            body=body_bytes,
            access_key_id=access_key_id,
            secret_access_key=secret_key,
            region='cn-north-1',
            service='rtc',
        )

        # 参考: https://www.volcengine.com/docs/6348/69828
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f'https://{host}',
                params=params,
                headers=signed_headers,
                content=body_bytes,
            )
        try:
            return resp.json()
        except ValueError:
            return {'raw': resp.text, 'status': resp.status_code}

    result = await wrapped_response_async('proxy', logic, contain_response_metadata=False)
    return JSONResponse(content=result)


@app.api_route('/getScenes', methods=['GET', 'POST', 'OPTIONS'])
async def get_scenes(request: Request):
    if request.method == 'OPTIONS':
        return JSONResponse(content=None, status_code=204)

    async def logic():
        scenes = []
        for scene_name, data in Scenes.items():
            scene_config = data.get('SceneConfig', {}) or {}
            rtc_config = data.get('RTCConfig', {}) or {}
            voice_chat = data.get('VoiceChat', {}) or {}

            app_id = rtc_config.get('AppId')
            room_id = rtc_config.get('RoomId')
            user_id = rtc_config.get('UserId')
            app_key = rtc_config.get('AppKey')
            token = rtc_config.get('Token')

            assert_(app_id, f'{scene_name} 场景的 RTCConfig.AppId 不能为空')

            room_id = room_id or str(uuid.uuid4())
            user_id = user_id or str(uuid.uuid4())
            _sync_rtc_identity(rtc_config, voice_chat, room_id, user_id)

            if app_key:
                rtc_config['Token'] = _generate_rtc_token(app_id, app_key, room_id, user_id)
            else:
                assert_(token, f'{scene_name} 场景的 RTCConfig.Token 不能为空；如需服务端自动生成 Token，请补充 RTCConfig.AppKey')

            if False and app_id and (not token or not user_id or not room_id):
                room_id = room_id or str(uuid.uuid4())
                user_id = user_id or str(uuid.uuid4())
                rtc_config['RoomId'] = room_id
                voice_chat['RoomId'] = room_id

                rtc_config['UserId'] = user_id
                agent_config = voice_chat.setdefault('AgentConfig', {})
                target_user_ids = agent_config.setdefault('TargetUserId', [user_id])
                if target_user_ids:
                    target_user_ids[0] = user_id
                else:
                    target_user_ids.append(user_id)

                assert_(app_key, f'自动生成 Token 时, {scene_name} 场景的 AppKey 不可为空')
                key = AccessToken(app_id, app_key, room_id, user_id)
                key.add_privilege(Privileges.PrivSubscribeStream, 0)
                key.add_privilege(Privileges.PrivPublishStream, 0)
                key.expire_time(int(time.time()) + 24 * 3600)
                rtc_config['Token'] = key.serialize()

            cfg = voice_chat.get('Config', {}) or {}
            llm_cfg = cfg.get('LLMConfig', {}) or {}
            vision_cfg = llm_cfg.get('VisionConfig', {}) or {}
            snapshot_cfg = vision_cfg.get('SnapshotConfig', {}) or {}
            avatar_cfg = cfg.get('AvatarConfig', {}) or {}
            agent_cfg = voice_chat.get('AgentConfig', {}) or {}

            scene_config['id'] = scene_name
            scene_config['botName'] = agent_cfg.get('UserId')
            scene_config['isInterruptMode'] = cfg.get('InterruptMode') == 0
            scene_config['isVision'] = vision_cfg.get('Enable')
            scene_config['isScreenMode'] = snapshot_cfg.get('StreamType') == 1
            scene_config['isAvatarScene'] = avatar_cfg.get('Enabled')
            scene_config['avatarBgUrl'] = avatar_cfg.get('BackgroundUrl')

            rtc_config.pop('AppKey', None)
            scenes.append({
                'scene': scene_config or {},
                'rtc': rtc_config,
            })
        return {'scenes': scenes}

    result = await wrapped_response_async('getScenes', logic, contain_response_metadata=True)
    return JSONResponse(content=result)


if __name__ == '__main__':
    port = int(os.environ.get('AIGC_SERVER_PORT', os.environ.get('PORT', '3002')))
    print(f'AIGC Server is running at http://localhost:{port}')
    uvicorn.run('app:app', host='0.0.0.0', port=port, reload=True)
