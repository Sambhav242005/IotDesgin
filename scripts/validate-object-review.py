"""Browser QA against the local validation scan; excludes one region then restores it."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scan', required=True)
    args = parser.parse_args()
    output = Path('data/benchmark/semantic-review')
    output.mkdir(parents=True, exist_ok=True)
    base = f'https://127.0.0.1:8443/desktop/scans/{args.scan}'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 1100})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        response = page.goto(base + '/review')
        assert response.status == 200
        expect(page.locator('#frame option')).to_have_count(12)
        metadata = context.request.get(base + '/objects').json()
        assert not metadata['review']['excluded_ids'], 'Use a new validation scan with no existing review'
        candidates = [(index, obj) for index, frame in enumerate(metadata['segmentation']['frames'])
                      for obj in frame['objects'] if obj.get('point_count', 0) > 0
                      and (obj.get('label') or '').lower() in {'person', 'man', 'woman'}]
        index, obj = max(candidates, key=lambda pair: pair[1]['point_count'])
        page.locator('#frame').select_option(str(index))
        checkbox = page.locator(f'input[id="{obj["id"]}"]')
        checkbox.check()
        with page.expect_response(lambda response: response.url.endswith('/objects/review')
                                  and response.request.method == 'POST') as pending:
            page.locator('#save').click()
        result = pending.value.json()
        assert pending.value.status == 200
        assert result['point_count_after'] < result['point_count_before']
        expect(page.locator('#status')).to_contain_text('Saved.')
        page.locator(f'label[for="{obj["id"]}"]').hover()
        page.wait_for_function('document.querySelector("#highlight").complete && '
                               'document.querySelector("#highlight").naturalWidth > 0')
        page.screenshot(path=str(output / 'review-desktop.png'), full_page=True)
        page.locator('#clear').click()
        with page.expect_response(lambda response: response.url.endswith('/objects/review')
                                  and response.request.method == 'POST') as pending:
            page.locator('#save').click()
        restored = pending.value.json()
        assert restored['point_count_after'] == result['point_count_before']
        expect(page.locator('#status')).to_contain_text('Saved.')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output / 'review-mobile.png'), full_page=True)
        assert not errors, errors
        (output / 'results.json').write_text(json.dumps({'scan_id': args.scan, 'excluded_object': obj['id'],
                                                       'filtered': result, 'restored': restored,
                                                       'page_errors': errors}, indent=2))
        print(json.dumps({'before': result['point_count_before'], 'filtered': result['point_count_after'],
                          'restored': restored['point_count_after'], 'page_errors': errors}, indent=2))
        browser.close()


if __name__ == '__main__':
    main()
