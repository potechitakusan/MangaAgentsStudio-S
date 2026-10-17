'use strict';
// Frame behaviour: collapsible library, side tabs, and the page/panel tab that only makes sense once a work has pages.
const TABS = ['general', 'work', 'page'];

function setLibraryCollapsed(collapsed) {
  document.querySelector('main').classList.toggle('library-collapsed', collapsed);
  const b = $('library-toggle');
  b.textContent = collapsed ? '»' : '«';
  b.setAttribute('aria-expanded', String(!collapsed));
  b.setAttribute('aria-label', collapsed ? '作品一覧を開く' : '作品一覧を閉じる');
  b.title = b.getAttribute('aria-label');
  storeValue('manga-library-collapsed', collapsed ? '1' : '0');
}
$('library-toggle').onclick = () => setLibraryCollapsed(!document.querySelector('main').classList.contains('library-collapsed'));
setLibraryCollapsed(storedValue('manga-library-collapsed') === '1');

function showTab(name, focus = false) {
  if (!TABS.includes(name)) name = 'work';
  for (const tab of TABS) {
    const button = $('tab-btn-' + tab), selected = tab === name;
    button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
    $('tab-' + tab).hidden = !selected;
    if (selected && focus) button.focus();
  }
  storeValue('manga-side-tab', name);
}
for (const tab of TABS) {
  $('tab-btn-' + tab).onclick = () => showTab(tab);
  $('tab-btn-' + tab).onkeydown = e => {
    const step = {ArrowRight: 1, ArrowLeft: -1}[e.key];
    if (step) { e.preventDefault(); showTab(TABS[(TABS.indexOf(tab) + step + TABS.length) % TABS.length], true); }
  };
}
showTab(storedValue('manga-side-tab') || 'work');

// The page/panel tab is meaningless until a project has pages.
const syncPageTab = () => { $('page-tab-body').hidden = $('studio').hidden; $('page-tab-empty').hidden = !$('studio').hidden; };
new MutationObserver(syncPageTab).observe($('studio'), {attributes: true, attributeFilter: ['hidden']});
syncPageTab();

