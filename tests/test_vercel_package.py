import json
from pathlib import Path
import unittest
from scripts.prepare_vercel import prepare
from test_research import temporary_directory


class VercelPackageTests(unittest.TestCase):
    def test_static_package_includes_desk_and_routes_api_to_fixed_backend(self):
        with temporary_directory() as tmp:
            root=prepare('https://backend.example.com',Path(tmp)/'site')
            self.assertIn('id="paper-form"',(root/'index.html').read_text(encoding='utf-8'))
            self.assertTrue((root/'auth.js').exists())
            self.assertEqual(json.loads((root/'vercel.json').read_text())['rewrites'][0]['destination'],'https://backend.example.com/api/:path*')
            self.assertFalse((root/'.env').exists());self.assertFalse((root/'data').exists())

    def test_backend_must_be_https_origin_without_secrets_or_query(self):
        for url in ['http://backend.example.com','https://secret@backend.example.com','https://backend.example.com/api','https://backend.example.com?token=secret']:
            with self.subTest(url=url),self.assertRaises(ValueError):prepare(url,'unused')
