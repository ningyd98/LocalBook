/**
 * ReadFlow Safari Extension - Background Script
 * 接收来自 content script 的消息，转发到 native app
 */

(function() {
  'use strict';

  // Native messaging 端口
  let nativePort = null;
  const NATIVE_APP_ID = 'com.readflow.capture';

  // 连接到 native app
  function connectNativeApp() {
    if (nativePort) {
      return nativePort;
    }

    try {
      nativePort = browser.runtime.connectNative(NATIVE_APP_ID);

      nativePort.onMessage.addListener((message) => {
        // Native messages may contain captured page text; never log payloads.
      });

      nativePort.onDisconnect.addListener(() => {
        console.log('ReadFlow: Disconnected from native app');
        nativePort = null;

        // 5秒后重连
        setTimeout(connectNativeApp, 5000);
      });

      console.log('ReadFlow: Connected to native app');
      return nativePort;
    } catch (error) {
      console.error('ReadFlow: Failed to connect to native app:', error);
      nativePort = null;
      return null;
    }
  }

  // 监听来自 content script 的消息
  browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    // Selection payloads contain user-captured content; never log the message.

    if (message.type === 'selection') {
      // 尝试连接 native app
      const port = connectNativeApp();

      if (port) {
        // 转发到 native app
        try {
          port.postMessage(message);
          sendResponse({ success: true });
        } catch (error) {
          console.error('ReadFlow: Failed to send to native app:', error);
          sendResponse({ success: false, error: error.message });
        }
      } else {
        // Fallback: 使用 localStorage 作为临时存储
        // Native app 可以通过 App Groups 读取
        try {
          const selections = JSON.parse(localStorage.getItem('readflow_selections') || '[]');
          selections.push(message);

          // 只保留最近 50 条
          if (selections.length > 50) {
            selections.shift();
          }

          localStorage.setItem('readflow_selections', JSON.stringify(selections));
          sendResponse({ success: true, fallback: 'localStorage' });
        } catch (error) {
          console.error('ReadFlow: Failed to save to localStorage:', error);
          sendResponse({ success: false, error: error.message });
        }
      }

      // 返回 true 表示异步响应
      return true;
    }
  });

  // 启动时连接 native app
  connectNativeApp();

  console.log('ReadFlow: Background script loaded');
})();
