import json
import io
from pathlib import Path
import unittest
from unittest.mock import patch
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

    def test_verification_failure_preserves_existing_deployment(self):
        for response in [b'An error occurred',b'{"status":"down"}',b'{"status":"ok"}']:
            with self.subTest(response=response),temporary_directory() as tmp:
                target=Path(tmp)/'site';target.mkdir()
                config=target/'vercel.json';config.write_text('existing')
                with patch('scripts.prepare_vercel.urlopen',side_effect=lambda *a,**k:io.BytesIO(response)):
                    with self.assertRaisesRegex(ValueError,'Backend verification failed'):
                        prepare('https://backend.example.com',target,verify=True)
                self.assertEqual(config.read_text(),'existing')

    def test_verified_public_backend_generates_frontend(self):
        with temporary_directory() as tmp:
            with patch('scripts.prepare_vercel.urlopen',side_effect=[io.BytesIO(b'{"status":"ok"}'),io.BytesIO(b'{"public_mode":true}')]) as request:
                target=prepare('https://backend.example.com',Path(tmp)/'site',verify=True)
            self.assertEqual(request.call_count,2)
            self.assertTrue((target/'index.html').exists())
