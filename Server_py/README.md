# Python Server

Node.js 版本的纯 Python 等价实现, 使用 FastAPI + httpx + uvicorn, 接口与端口完全兼容前端。

## 启动命令

```
# 推荐先创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

python app.py
# 或: uvicorn app:app --host 0.0.0.0 --port 3001 --reload
```

启动后监听 `http://localhost:3001`, 与 Node 版本完全一致。
FastAPI 自带 Swagger 文档可访问: http://localhost:3001/docs

## 目录结构

```
Server_py/
├── app.py              # FastAPI 主入口, 与 Node 版本 app.js 等价
├── signer.py           # 火山引擎 OpenAPI V4 签名
├── token_manager.py    # RTC AccessToken 二进制序列化, 与 Node 版本兼容
├── util.py             # 通用工具 (读取场景 JSON / 校验 / 响应包装)
├── requirements.txt
└── scenes/             # 场景 JSON 配置目录
    └── Custom.json
```

## 使用须知
Python 服务启动时会自动读取 `Server_py/scenes` 下的所有文件作为可用的场景, 并通过接口 API 返回相关信息。

因此，您需要：
1. 在 `Server_py/scenes` 目录下参考其它 JSON 的格式, 自定义创建一个 `xxxx.json` 文件，用于描述您的场景，其中 xxxx 为场景名称。
2. 确保您的 `.json` 文件符合模版定义(可参考 Custom.json), 大小写敏感。
3. 新增场景 JSON 后须重启服务, 保证场景信息被正常读取 (uvicorn `--reload` 会自动重载)。
4. JSON 文件中, 若 `RTCConfig.RoomId`、`RTCConfig.UserId`、`RTCConfig.Token` 其中之一未填写, 服务将自动生成对应的值以保证对话可以正常启动。

## 相关参数获取
- AccountConfig
    - 可在 https://console.volcengine.com/iam/keymanage/ 获取 AK/SK。
- RTCConfig
    - AppId、AppKey 可从 https://console.volcengine.com/rtc/aigc/listRTC 中获取。
    - RoomId、UserId 可自定义也可不填，交由服务端生成。
- VoiceChat
    - 可参考 https://www.volcengine.com/docs/6348/1558163 中参数描述。
    - 可通过 [快速跑通 Demo](https://console.volcengine.com/rtc/aigc/run?s=g) 快速获取参数。

## 接口

### `GET /getScenes`
返回所有场景配置, 必要时自动生成 RoomId / UserId / Token。

### `POST /proxy?Action=xxx&Version=2024-12-01`
代理火山 RTC OpenAPI 请求, body 中需带 `SceneID` 指定场景。
当前支持的 Action: `StartVoiceChat`, `StopVoiceChat`。

## 注意
- 相关错误会通过服务端接口返回。
- 服务会根据您配置的 `VoiceChat` 中是否存在视觉模型相关的配置返回相关信息给前端页面, 从而控制相关 UI 是否展示。
- 使用时请留意相关服务已开通。
