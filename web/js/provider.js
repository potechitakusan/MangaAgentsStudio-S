'use strict';
// Which production AI (Strata or Grok) runs the next job.
const llmProvider = () => $('llm-provider').value || 'strata';
const llmLabel = () => llmProvider() === 'grok' ? 'Grok' : 'Strata';
function renderProvider() {
  document.querySelectorAll('.llm-name').forEach(n => n.textContent = llmLabel());
  $('llm-note').textContent = llmProvider() === 'grok'
    ? 'Grok CLIのログインを使用します。対象の原作・台帳・指示をGrokのクラウドへ送信します。利用上限・費用はGrok側の契約に従います。'
    : 'Strataをローカルで使用します。字コンテ・修正・翻訳・点検・衣装整理に適用されます。';
}
function setupProviders(items) {
  for (const item of items || []) {
    const option = [...$('llm-provider').options].find(o => o.value === item.id);
    if (option) { option.disabled = !item.available; if (!item.available) option.textContent += '（CLI未検出）'; }
  }
  let preferred = 'strata';
  try { preferred = localStorage.getItem('manga-llm-provider') || 'strata'; } catch (_) {}
  if ([...$('llm-provider').options].some(o => o.value === preferred && !o.disabled)) $('llm-provider').value = preferred;
  renderProvider();
}
$('llm-provider').onchange = () => {
  try { localStorage.setItem('manga-llm-provider', llmProvider()); } catch (_) {}
  renderProvider();
};
