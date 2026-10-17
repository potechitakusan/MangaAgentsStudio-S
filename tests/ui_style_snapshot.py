"""Record or compare the computed layout/visual styles of every element in the main UI states.
Used to prove that moving CSS or JavaScript between files changed nothing visible.
  record : python tests/ui_style_snapshot.py record OUT.json [port]
  compare: python tests/ui_style_snapshot.py compare BEFORE.json AFTER.json
Needs the project's Playwright: PYTHONPATH=.runtime/test-deps PLAYWRIGHT_BROWSERS_PATH=.runtime/browsers"""
import json
import sys
from pathlib import Path

PROPS = ('display position top left right bottom width height min-width max-width min-height max-height margin-top margin-right margin-bottom margin-left '
         'padding-top padding-right padding-bottom padding-left border-top-width border-right-width border-bottom-width border-left-width border-top-style border-top-color '
         'border-bottom-color border-top-left-radius background-color color font-size font-weight font-family line-height text-align white-space overflow-x overflow-y '
         'flex-direction flex-grow flex-shrink flex-basis align-items justify-content gap grid-template-columns grid-template-rows grid-column-start z-index opacity '
         'visibility box-shadow cursor outline-style transform object-fit resize').split()

COLLECT = '''(props) => {
  const out = {};
  const seen = new Map();
  const walk = (node, path) => {
    const tag = node.tagName.toLowerCase();
    const key = node.id ? tag + '#' + node.id : path + '>' + tag + ':' + ((seen.get(path + tag) || 0) + 1);
    seen.set(path + tag, (seen.get(path + tag) || 0) + 1);
    const style = getComputedStyle(node), values = {};
    for (const p of props) values[p] = style.getPropertyValue(p);
    const box = node.getBoundingClientRect();
    values.box = [Math.round(box.x), Math.round(box.y + window.scrollY), Math.round(box.width), Math.round(box.height)].join(',');
    out[key] = values;
    for (const child of node.children) walk(child, key);
  };
  walk(document.body, 'body');
  return out;
}'''


def record(out_path, port):
    from playwright.sync_api import sync_playwright, expect
    base = f'http://127.0.0.1:{port}'
    states = {}
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        page.on('dialog', lambda d: d.accept())

        def snap(name):
            page.wait_for_timeout(450)
            states[name] = page.evaluate(COLLECT, PROPS)

        def open_details():
            page.evaluate('() => document.querySelectorAll("details").forEach(d => { d.open = true; })')

        def open_work(title, ready):
            page.get_by_role('button', name=title, exact=False).first.click()
            expect(page.locator(ready[0])).to_contain_text(ready[1], timeout=20000)
            page.wait_for_timeout(600)

        page.goto(base, wait_until='networkidle')
        page.evaluate('() => { localStorage.clear(); }')
        page.reload(wait_until='networkidle')
        open_work('lantern-12', ('#subtitle', '12 / 24'))
        page.locator('#tab-btn-general').click(); snap('general-1920')
        page.locator('#tab-btn-work').click(); open_details(); snap('work-1920')
        page.locator('#page-hotspots button').nth(1).click(); open_details(); snap('page-panels-1920')
        page.locator('#library-toggle').click(); snap('library-collapsed')
        page.locator('#library-toggle').click()
        page.locator('.zoom-button[data-zoom="2"]').click(); snap('zoom-2')
        page.locator('.zoom-button[data-zoom="1"]').click()
        page.locator('#produce-page').click(); snap('dialog-produce'); page.locator('#produce-cancel').click()
        page.locator('#delete-project').evaluate('b => b.scrollIntoView()') if page.locator('#delete-project').count() else None
        page.locator('#tab-btn-work').click(); page.locator('#delete-project').click(); snap('dialog-delete'); page.locator('#delete-cancel').click()
        page.locator('#new-project').click(); snap('dialog-create'); page.locator('#close-dialog').click()
        for width in (1000, 700):
            page.set_viewport_size({'width': width, 'height': 900})
            page.locator('#tab-btn-page').click(); snap(f'page-{width}')
            page.locator('#tab-btn-work').click(); snap(f'work-{width}')
        page.set_viewport_size({'width': 1920, 'height': 1080})
        open_work('wholepage-demo', ('#mode-label', 'ページ一括'))
        page.locator('#tab-btn-page').click(); open_details(); snap('page-whole-1920')
        browser.close()
    Path(out_path).write_text(json.dumps(states, ensure_ascii=False), encoding='utf-8')
    print('recorded', {k: len(v) for k, v in states.items()})


def compare(before_path, after_path):
    before, after = (json.loads(Path(p).read_text(encoding='utf-8')) for p in (before_path, after_path))
    problems = 0
    for state in before:
        a, b = ({k: v for k, v in states.get(state, {}).items() if '>script:' not in k} for states in (before, after))  # script tags are not UI
        only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        changed = [(k, [(prop, a[k][prop], b[k][prop]) for prop in a[k] if a[k][prop] != b[k].get(prop)]) for k in a if k in b and a[k] != b[k]]
        if only_a or only_b or changed:
            problems += 1
            print(f'[{state}] elements only before: {len(only_a)}, only after: {len(only_b)}, changed: {len(changed)}')
            for k in only_a[:5]: print('   - removed', k)
            for k in only_b[:5]: print('   + added  ', k)
            for k, diffs in changed[:8]: print('   ~', k, diffs[:4])
        else:
            print(f'[{state}] identical ({len(a)} elements)')
    return problems


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'record':
        record(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '8765')
    else:
        sys.exit(1 if compare(sys.argv[2], sys.argv[3]) else 0)
