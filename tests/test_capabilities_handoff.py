"""Controller capability flags must not turn Map's live inventory offline."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cartographer.client import PrintClient, validate_capabilities
from cartographer.model import media_profiles
from print_contract import capabilities
from jsonschema import ValidationError


class CapabilitiesHandoffTest(unittest.TestCase):
    def setUp(self):
        self.carousel = json.loads((ROOT/'cartographer/fixtures/carousel.json').read_text())
        self.advertised = capabilities(self.carousel, media_profiles())
        # The real controller advertises this additive flag alongside map handoff.
        self.advertised['implemented'].update(pdf_upload=True, pdf_handoff=True,
                                              per_ink_widths=True, overlap_strokes=True)

    def test_map_accepts_controller_overlap_support_and_keeps_live_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            client = PrintClient('http://controller.test:9191', directory)
            def read(path):
                return copy.deepcopy(self.advertised if path == '/api/v1/capabilities' else self.carousel)
            with patch.object(client, 'read', side_effect=read):
                result = client.inventory()
            self.assertFalse(result['offline'])
            self.assertEqual(result['source'], 'live capabilities')
            self.assertTrue(result['capabilities']['implemented']['overlap_strokes'])
            self.assertTrue(result['capabilities']['implemented']['pdf_handoff'])
            self.assertEqual(result['carousel'], self.carousel)
            self.assertEqual(json.loads((Path(directory)/'inventory-snapshot.json').read_text()), result)

    def test_older_capabilities_remain_valid_and_unknown_or_mistyped_flags_fail(self):
        old = copy.deepcopy(self.advertised)
        del old['implemented']['overlap_strokes']
        validate_capabilities(old)
        for key, value in (('overlap_strokes', 'yes'), ('unknown_feature', True)):
            bad = copy.deepcopy(self.advertised)
            bad['implemented'][key] = value
            with self.assertRaises(ValidationError):
                validate_capabilities(bad)


if __name__ == '__main__':
    unittest.main()
