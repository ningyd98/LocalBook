/**
 * ReadFlow Safari Extension - Content Script
 * 监听页面选中文本事件，捕获文本、URL、标题和上下文
 */

(function() {
  'use strict';

  // 防抖函数
  function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
      const later = () => {
        clearTimeout(timeout);
        func(...args);
      };
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
    };
  }

  // 获取选中文本前的上下文（最多 200 字符）
  function getContextBefore(selection) {
    try {
      const range = selection.getRangeAt(0);
      const container = range.startContainer;

      // 获取 startContainer 的完整文本内容
      let fullText = '';
      if (container.nodeType === Node.TEXT_NODE) {
        // 向上查找包含更多文本的父节点
        let parent = container.parentElement;
        while (parent && parent !== document.body) {
          if (parent.textContent && parent.textContent.length > 200) {
            fullText = parent.textContent;
            break;
          }
          parent = parent.parentElement;
        }

        if (!fullText) {
          fullText = container.textContent || '';
        }
      } else {
        fullText = container.textContent || '';
      }

      // 找到选中文本在完整文本中的位置
      const selectedText = selection.toString();
      const selectionIndex = fullText.indexOf(selectedText);

      if (selectionIndex > 0) {
        const start = Math.max(0, selectionIndex - 200);
        return fullText.substring(start, selectionIndex);
      }

      return '';
    } catch (error) {
      console.error('ReadFlow: Failed to get context before:', error);
      return '';
    }
  }

  // 获取选中文本后的上下文（最多 200 字符）
  function getContextAfter(selection) {
    try {
      const range = selection.getRangeAt(0);
      const container = range.endContainer;

      // 获取 endContainer 的完整文本内容
      let fullText = '';
      if (container.nodeType === Node.TEXT_NODE) {
        // 向上查找包含更多文本的父节点
        let parent = container.parentElement;
        while (parent && parent !== document.body) {
          if (parent.textContent && parent.textContent.length > 200) {
            fullText = parent.textContent;
            break;
          }
          parent = parent.parentElement;
        }

        if (!fullText) {
          fullText = container.textContent || '';
        }
      } else {
        fullText = container.textContent || '';
      }

      // 找到选中文本在完整文本中的位置
      const selectedText = selection.toString();
      const selectionIndex = fullText.indexOf(selectedText);

      if (selectionIndex >= 0) {
        const end = selectionIndex + selectedText.length;
        return fullText.substring(end, Math.min(end + 200, fullText.length));
      }

      return '';
    } catch (error) {
      console.error('ReadFlow: Failed to get context after:', error);
      return '';
    }
  }

  // 处理文本选中事件
  function handleTextSelection() {
    const selection = window.getSelection();
    const selectedText = selection.toString().trim();

    if (!selectedText || selectedText.length === 0) {
      return;
    }

    // 获取上下文
    const contextBefore = getContextBefore(selection);
    const contextAfter = getContextAfter(selection);

    // 获取当前滚动位置
    const scrollY = window.scrollY || window.pageYOffset;

    // 构造消息
    const message = {
      type: 'selection',
      text: selectedText,
      url: window.location.href,
      title: document.title,
      contextBefore: contextBefore,
      contextAfter: contextAfter,
      scrollY: scrollY,
      timestamp: new Date().toISOString()
    };

    // 发送到 background script
    browser.runtime.sendMessage(message)
      .then(() => {
        console.log('ReadFlow: Selection captured successfully');
      })
      .catch((error) => {
        console.error('ReadFlow: Failed to send message:', error);
      });
  }

  // 监听 mouseup 事件（防抖 300ms）
  const debouncedHandler = debounce(handleTextSelection, 300);
  document.addEventListener('mouseup', debouncedHandler);

  // 监听键盘选择（Shift + 方向键）
  document.addEventListener('keyup', (event) => {
    if (event.shiftKey) {
      debouncedHandler();
    }
  });

  console.log('ReadFlow: Content script loaded');
})();
