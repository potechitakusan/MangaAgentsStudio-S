'use strict';
// Adds a "コピー" button to every .copy-block in the guides.
document.querySelectorAll('.copy-block').forEach(block => {
  const pre = block.querySelector('pre'), button = document.createElement('button');
  button.type = 'button'; button.textContent = 'コピー';
  button.onclick = async () => {
    try { await navigator.clipboard.writeText(pre.textContent); button.textContent = 'コピーしました'; }
    catch (_) { const range = document.createRange(); range.selectNodeContents(pre); getSelection().removeAllRanges(); getSelection().addRange(range); button.textContent = '選択しました（Ctrl+C）'; }
    setTimeout(() => { button.textContent = 'コピー'; }, 1800);
  };
  block.append(button);
});
