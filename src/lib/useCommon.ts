/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useEffect, useState, useRef } from 'react';
import { useSelector, useDispatch } from 'react-redux';

import VERTC, { MediaType } from '@volcengine/rtc';
import { Message, Modal } from '@arco-design/web-react';
import CustomerServiceIcon from '@/assets/img/CustomerService.svg';
import RtcClient from '@/lib/RtcClient';
import {
  clearCurrentMsg,
  clearHistoryMsg,
  localJoinRoom,
  localLeaveRoom,
  RTCConfig,
  updateAIGCState,
  updateLocalUser,
} from '@/store/slices/room';
import { setCurrentSid, setConversations } from '@/store/slices/history';
import * as llmApi from '@/lib/llmServerApi';

import useRtcListeners from '@/lib/listenerHooks';
import { RootState } from '@/store';

import {
  updateMediaInputs,
  updateSelectedDevice,
  setDevicePermissions,
} from '@/store/slices/device';
import logger from '@/utils/logger';

/** 服务端下发的 icon 若非 http 链接，则用本地打包资源兜底 */
export function resolveIcon(icon?: string): string {
  if (!icon || !icon.startsWith('http')) return CustomerServiceIcon;
  return icon;
}

export const ABORT_VISIBILITY_CHANGE = 'abortVisibilityChange';
export interface FormProps {
  username: string;
  roomId: string;
  publishAudio: boolean;
}

export const useScene = () => {
  const { scene, sceneConfigMap } = useSelector((state: RootState) => state.room);
  const config = sceneConfigMap[scene] || {};
  return { ...config, icon: resolveIcon(config.icon) };
};

export const useRTC = () => {
  const { scene, rtcConfigMap } = useSelector((state: RootState) => state.room);
  return rtcConfigMap[scene] || {};
};

export const useDeviceState = () => {
  const dispatch = useDispatch();
  const room = useSelector((state: RootState) => state.room);
  const localUser = room.localUser;
  const isAudioPublished = localUser.publishAudio;
  const isVideoPublished = localUser.publishVideo;
  const isScreenPublished = localUser.publishScreen;
  const queryDevices = async (type: MediaType) => {
    const mediaDevices = await RtcClient.getDevices({
      audio: type === MediaType.AUDIO,
      video: type === MediaType.VIDEO,
    });
    if (type === MediaType.AUDIO) {
      dispatch(
        updateMediaInputs({
          audioInputs: mediaDevices.audioInputs,
        })
      );
      dispatch(
        updateSelectedDevice({
          selectedMicrophone: mediaDevices.audioInputs[0]?.deviceId,
        })
      );
    } else {
      dispatch(
        updateMediaInputs({
          videoInputs: mediaDevices.videoInputs,
        })
      );
      dispatch(
        updateSelectedDevice({
          selectedCamera: mediaDevices.videoInputs[0]?.deviceId,
        })
      );
    }
    return mediaDevices;
  };

  const switchMic = async (controlPublish = true) => {
    if (controlPublish) {
      await (!isAudioPublished
        ? RtcClient.publishStream(MediaType.AUDIO)
        : RtcClient.unpublishStream(MediaType.AUDIO));
    }
    await queryDevices(MediaType.AUDIO);
    await (!isAudioPublished ? RtcClient.startAudioCapture() : RtcClient.stopAudioCapture());
    dispatch(
      updateLocalUser({
        publishAudio: !isAudioPublished,
      })
    );
  };

  const switchCamera = async (controlPublish = true) => {
    if (controlPublish) {
      await (!isVideoPublished
        ? RtcClient.publishStream(MediaType.VIDEO)
        : RtcClient.unpublishStream(MediaType.VIDEO));
    }
    await queryDevices(MediaType.VIDEO);
    await (!isVideoPublished ? RtcClient.startVideoCapture() : RtcClient.stopVideoCapture());
    dispatch(
      updateLocalUser({
        publishVideo: !isVideoPublished,
      })
    );
  };

  const switchScreenCapture = async (controlPublish = true) => {
    try {
      !isScreenPublished
        ? sessionStorage.setItem(ABORT_VISIBILITY_CHANGE, 'true')
        : sessionStorage.removeItem(ABORT_VISIBILITY_CHANGE);
      if (controlPublish) {
        await (!isScreenPublished
          ? RtcClient.publishScreenStream(MediaType.VIDEO)
          : RtcClient.unpublishScreenStream(MediaType.VIDEO));
      }
      await (!isScreenPublished ? RtcClient.startScreenCapture() : RtcClient.stopScreenCapture());
      dispatch(
        updateLocalUser({
          publishScreen: !isScreenPublished,
        })
      );
    } catch {
      console.warn('Not Authorized.');
    }
    sessionStorage.removeItem(ABORT_VISIBILITY_CHANGE);
    return false;
  };

  return {
    isAudioPublished,
    isVideoPublished,
    isScreenPublished,
    switchMic,
    switchCamera,
    switchScreenCapture,
  };
};

export const useGetDevicePermission = () => {
  const [permission, setPermission] = useState<{
    audio: boolean;
  }>();

  const dispatch = useDispatch();

  useEffect(() => {
    (async () => {
      const permission = await RtcClient.checkPermission();
      dispatch(setDevicePermissions(permission));
      setPermission(permission);
    })();
  }, [dispatch]);
  return permission;
};

export const useJoin = (): [boolean, () => Promise<void | boolean>] => {
  const devicePermissions = useSelector((state: RootState) => state.device.devicePermissions);
  const room = useSelector((state: RootState) => state.room);
  const currentSid = useSelector((state: RootState) => state.history.currentSid);
  // useRef 让闭包能拿到最新值, 不被首次渲染的快照锁死
  const currentSidRef = useRef(currentSid);
  currentSidRef.current = currentSid;

  const dispatch = useDispatch();

  const { id } = useScene();
  const rtc = useRTC();
  const { switchMic } = useDeviceState();
  const [joining, setJoining] = useState(false);
  const listeners = useRtcListeners();

  /**
   * 确保有一个 sid 用于本次通话:
   * - history.currentSid 有值 → 续聊 (侧栏已经把历史灌进 msgHistory)
   * - 没有 → 现场向 LLMServer 申请, 写进 store, 顺便刷一次列表让侧栏能看到
   */
  const ensureSessionId = async (): Promise<string> => {
    if (currentSidRef.current) return currentSidRef.current;
    try {
      const sid = await llmApi.createContext();
      dispatch(setCurrentSid(sid));
      llmApi
        .listConversations()
        .then((list) => dispatch(setConversations(list)))
        .catch(() => {});
      return sid;
    } catch (e) {
      console.warn('[history] 创建 context 失败, 退回 stateless 模式', e);
      return '';
    }
  };

  const handleAIGCModeStart = async () => {
    const sid = await ensureSessionId();
    if (room.isAIGCEnable) {
      await RtcClient.stopAgent(id);
      dispatch(clearCurrentMsg());
      await RtcClient.startAgent(id, sid || undefined);
    } else {
      await RtcClient.startAgent(id, sid || undefined);
    }
    dispatch(updateAIGCState({ isAIGCEnable: true }));
  };

  async function disPatchJoin(): Promise<boolean | undefined> {
    if (joining) {
      return;
    }

    const isSupported = await VERTC.isSupported();
    if (!isSupported) {
      Modal.error({
        title: '不支持 RTC',
        content: '您的浏览器可能不支持 RTC 功能，请尝试更换浏览器或升级浏览器后再重试。',
      });
      return;
    }

    const missingRTCFields = (['AppId', 'RoomId', 'UserId', 'Token'] as (keyof RTCConfig)[]).filter(
      (key) => !rtc?.[key]
    );
    if (missingRTCFields.length) {
      Modal.error({
        title: 'RTC 配置未加载',
        content: `未获取到 ${missingRTCFields.join(
          ', '
        )}，请确认 AIGC 后端 /getScenes 已启动，并且前端 AIGC_PROXY_HOST 指向该后端。`,
      });
      return false;
    }

    RtcClient.basicInfo = {
      app_id: rtc.AppId,
      room_id: rtc.RoomId,
      user_id: rtc.UserId,
      token: rtc.Token,
    };

    setJoining(true);

    try {
      /** 1. Create RTC Engine */
      await RtcClient.createEngine();

      /** 2.1 Set events callbacks */
      RtcClient.addEventListeners(listeners);

      /** 2.2 RTC starting to join room */
      await RtcClient.joinRoom();
      /** 3. Set users' devices info */
      const mediaDevices = await RtcClient.getDevices({
        audio: true,
        video: false,
      });

      dispatch(
        localJoinRoom({
          roomId: RtcClient.basicInfo.room_id,
          user: {
            username: RtcClient.basicInfo.user_id,
            userId: RtcClient.basicInfo.user_id,
          },
        })
      );
      dispatch(
        updateSelectedDevice({
          selectedMicrophone: mediaDevices.audioInputs[0]?.deviceId,
          selectedCamera: mediaDevices.videoInputs[0]?.deviceId,
        })
      );
      dispatch(updateMediaInputs(mediaDevices));
    } catch (e) {
      setJoining(false);
      const message = e instanceof Error ? e.message : String(e || '');
      if (message.includes('token_error')) {
        Modal.error({
          title: 'RTC Token 校验失败',
          content:
            '请检查 Server_py/scenes/Custom.json 中 RTCConfig 的 AppId、AppKey、RoomId、UserId、Token 是否匹配。建议补充真实 AppKey，让后端自动生成 Token。',
        });
      } else {
        Message.error(`加入房间失败: ${message}`);
      }
      throw e;
    }

    setJoining(false);

    if (devicePermissions.audio) {
      try {
        await switchMic();
      } catch (e) {
        logger.debug('No permission for mic');
      }
    }

    try {
      await handleAIGCModeStart();
    } catch (e) {
      logger.debug('start AIGC failed:', e);
    }
  }

  return [joining, disPatchJoin];
};

export const useLeave = () => {
  const dispatch = useDispatch();
  const { id } = useScene();
  const idRef = useRef(id);
  idRef.current = id;

  return async function () {
    await Promise.all([
      RtcClient.stopAudioCapture(),
      RtcClient.stopScreenCapture(),
      RtcClient.stopVideoCapture(),
    ]);
    await RtcClient.stopAgent(idRef.current);
    await RtcClient.leaveRoom();
    dispatch(clearHistoryMsg());
    dispatch(clearCurrentMsg());
    dispatch(localLeaveRoom());
    dispatch(updateAIGCState({ isAIGCEnable: false }));
    // 通话结束后顺手刷一次列表, 让侧栏看到 last_message / updated_at 的更新
    llmApi
      .listConversations()
      .then((list) => dispatch(setConversations(list)))
      .catch(() => {});
  };
};
