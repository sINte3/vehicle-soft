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
