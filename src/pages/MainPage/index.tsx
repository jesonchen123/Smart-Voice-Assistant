/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useEffect } from 'react';
import { useDispatch } from 'react-redux';
import { Modal } from '@arco-design/web-react';
import Header from '@/components/Header';
import ResizeWrapper from '@/components/ResizeWrapper';
import Menu from './Menu';
import HistorySidebar from './HistorySidebar';
import { useIsMobile } from '@/utils/utils';
import Apis from '@/app/index';
import MainArea from './MainArea';
import { ABORT_VISIBILITY_CHANGE, useLeave } from '@/lib/useCommon';
import { RTCConfig, SceneConfig, updateRTCConfig, updateScene, updateSceneConfig } from '@/store/slices/room';
import styles from './index.module.less';

export default function () {

  const leaveRoom = useLeave();
  const dispatch = useDispatch();

  const getScenes = async () => {
    let scenes: {
      rtc: RTCConfig;
      scene: SceneConfig;
    }[] = [];
    try {
      ({ scenes = [] } = await Apis.Basic.getScenes());
    } catch (e) {
      Modal.error({
        title: '场景配置加载失败',
        content: '请确认 AIGC 后端服务已启动，并且前端 AIGC_PROXY_HOST 指向该服务。',
      });
      return;
    }
    if (!scenes.length) {
      Modal.error({
        title: '场景配置为空',
        content: '后端 /getScenes 未返回可用场景，请检查 Server_py/scenes 下的配置文件。',
      });
      return;
    }
    dispatch(updateScene(scenes[0].scene.id));
    dispatch(updateSceneConfig(
      scenes.reduce<Record<string, SceneConfig>>((prev, cur) => {
        prev[cur.scene.id] = cur.scene;
        return prev;
      }, {})
    ));
    dispatch(updateRTCConfig(
      scenes.reduce<Record<string, RTCConfig>>((prev, cur) => {
        prev[cur.scene.id] = cur.rtc;
        return prev;
      }, {})
    ));
  }

  useEffect(() => {
    getScenes();
    const isOriginalDemo = window.location.host.startsWith('localhost');
    const handler = () => {
      if (
        document.visibilityState === 'hidden' &&
        !sessionStorage.getItem(ABORT_VISIBILITY_CHANGE)
      ) {
        leaveRoom();
      }
    };
    !isOriginalDemo && document.addEventListener('visibilitychange', handler);
    return () => {
      !isOriginalDemo && document.removeEventListener('visibilitychange', handler);
    };
  }, []);

  return (
    <ResizeWrapper className={styles.container}>
      <Header />
      <div
        className={styles.main}
        style={{
          padding: useIsMobile() ? '' : '24px',
        }}
      >
        {useIsMobile() ? null : <HistorySidebar />}
        <div className={`${styles.mainArea} ${useIsMobile() ? styles.isMobile : ''}`}>
          <MainArea />
        </div>
        {useIsMobile() ? null : (
          <div className={styles.operationArea}>
            <Menu />
          </div>
        )}
      </div>
    </ResizeWrapper>
  );
}
