"""The embedded backend must be able to name its own private HLS URL.

Bilibili live (and anything else the backend repackages before the player can
read it) reaches the subtitle pipeline through `_private_hls_url()`, which reads
`args.host` and `args.port`. The desktop entry used to build its Namespace
without either field, so the ASR start died with

    AttributeError: 'Namespace' object has no attribute 'host'

and the session ran with no subtitle pipeline at all -- not late captions, no
captions. These tests pin the arguments the desktop actually launches with,
against the code that consumes them.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'desktop'))

import companion_entry  # noqa: E402  (path set above)


class BackendArgumentTests(unittest.TestCase):
    def test_the_desktop_arguments_name_the_private_hls_url(self):
        from companion.server import CompanionApplication

        with tempfile.TemporaryDirectory(prefix='lingerlens-desktop-args-') as temp:
            data = Path(temp)
            media, providers = companion_entry.runtime_paths(data)
            args = companion_entry.backend_arguments(media, providers, port=41234)
            companion = CompanionApplication(args, enable_native_control=False)
            companion.private_hls_token = 'test-token'

            self.assertEqual(
                companion._private_hls_url(),
                'http://127.0.0.1:41234/_private-hls/test-token/live.m3u8',
            )

    def test_the_arguments_never_serve_anything_but_loopback(self):
        with tempfile.TemporaryDirectory(prefix='lingerlens-desktop-args-') as temp:
            media, providers = companion_entry.runtime_paths(Path(temp))
            args = companion_entry.backend_arguments(media, providers)
            self.assertEqual(args.host, '127.0.0.1')
            # The socket is bound to port 0 and the real value is written back by
            # serve(), so the default must stay "not yet known" rather than
            # something that would look like a working address.
            self.assertEqual(args.port, 0)


if __name__ == '__main__':
    unittest.main()
