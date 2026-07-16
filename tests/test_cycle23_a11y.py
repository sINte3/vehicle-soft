# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 3: skip-link, content focus, filter labels."""
import re
import unittest

from tests.harness import app, reset_db, create_admin, create_org, login


class A11yTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()

    def _get(self, path):
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp.get_data(as_text=True)

    def test_skip_link_and_focus_target_present(self):
        html = self._get('/spare-parts/')
        self.assertIn('class="vs-skip-link"', html)
        self.assertIn('href="#vsContent"', html)
        self.assertIn('id="vsContent" tabindex="-1"', html)
        self.assertIn('id="vsScreenTitle" tabindex="-1"', html)

    def test_list_filters_have_label_associations(self):
        html = self._get('/spare-parts/')
        for fid in ('listOrgFilter', 'listDateFrom', 'listDateTo',
                    'spareRequestSearch', 'clientStatusFilter'):
            self.assertIn('for="{}"'.format(fid), html)
            self.assertIn('id="{}"'.format(fid), html)

    def test_reports_filters_have_label_associations(self):
        html = self._get('/spare-parts/reports')
        for fid in ('repDateFrom', 'repDateTo', 'orgSelect', 'eqSelect',
                    'repCategory'):
            self.assertIn('for="{}"'.format(fid), html)

    # ── CYCLE-2-3-HOTFIX F-001: skip link must be first in the tab order ──

    def _first_tab_focusable(self, html):
        """Return the first tab-focusable element tag in body DOM order.

        Mirrors browser tab-order rules for server-rendered markup: links
        with href, enabled form controls and buttons, plus anything with an
        explicit non-negative tabindex. tabindex="-1" (skip-link targets
        like #vsContent/#vsScreenTitle) is focusable but NOT tab-reachable,
        so it must not count.
        """
        body = html[html.index('<body'):]
        for m in re.finditer(
                r'<(a|button|input|select|textarea|[a-z][a-z0-9-]*)\b[^>]*>',
                body):
            tag_name = m.group(1)
            tag = m.group(0)
            ti = re.search(r'\btabindex="(-?\d+)"', tag)
            if ti:
                if int(ti.group(1)) >= 0:
                    return tag
                continue
            if tag_name == 'a' and 'href=' in tag:
                return tag
            if tag_name in ('button', 'input', 'select', 'textarea'):
                if 'disabled' in tag or 'type="hidden"' in tag:
                    continue
                return tag
        return None

    def test_first_tab_focusable_element_is_skip_link(self):
        # [REASON]: CYCLE-2-3-HOTFIX F-001 — after a normal page load the
        # first Tab must land on the skip link. base_next.html is the shared
        # layout for the whole app, so check other modules too, not only
        # Spare Parts (dashboard=transport, fuel=АЗС).
        for path in ('/spare-parts/', '/', '/fuel/'):
            html = self._get(path)
            first = self._first_tab_focusable(html)
            self.assertIsNotNone(first, path)
            self.assertIn('vs-skip-link', first,
                          '{}: first tab-focusable element is {}'.format(
                              path, first))

    def test_no_auto_focus_of_screen_title_on_load(self):
        # The removed DOMContentLoaded handler must not come back: no script
        # may move focus to #vsScreenTitle on page load. The element itself
        # stays (tabindex="-1") as an anchor/skip-link focus target only.
        for path in ('/spare-parts/', '/', '/fuel/'):
            html = self._get(path)
            self.assertIn('id="vsScreenTitle" tabindex="-1"', html, path)
            for script in re.findall(r'<script[^>]*>(.*?)</script>', html,
                                     re.DOTALL):
                if 'vsScreenTitle' in script:
                    self.assertNotIn('.focus(', script,
                                     '{}: script auto-focuses vsScreenTitle'
                                     .format(path))

    def test_no_unlabeled_controls_on_list_and_reports(self):
        # Same rule as the repo-side template sweep: every rendered
        # non-hidden input/select/textarea needs a label-for or aria-label.
        for path in ('/spare-parts/', '/spare-parts/reports',
                     '/spare-parts/maintenance', '/spare-parts/maintenance-norms'):
            html = self._get(path)
            label_for = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', html))
            for m in re.finditer(r'<(input|select|textarea)\b[^>]*>', html):
                tag = m.group(0)
                if 'type="hidden"' in tag:
                    continue
                id_m = re.search(r'\bid="([^"]+)"', tag)
                ok = ('aria-label' in tag) or (id_m and id_m.group(1) in label_for)
                self.assertTrue(ok, '{}: unlabeled control {}'.format(path, tag[:100]))


if __name__ == '__main__':
    unittest.main()
