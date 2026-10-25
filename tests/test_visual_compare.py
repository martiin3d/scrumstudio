import tempfile
from pathlib import Path
import unittest
from PIL import Image
from scripts.visual_compare import compare

class VisualCompareTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.a=self.root/'reference.png';self.b=self.root/'actual.png'
        Image.new('RGBA',(3,3),(255,255,255,255)).save(self.a);Image.new('RGBA',(3,3),(255,255,255,255)).save(self.b)
    def test_identical_and_cached_without_repeated_image_analysis(self):
        result=compare(self.a,self.b,self.root/'out');self.assertTrue(result['pixel_identical']);self.assertFalse(result['cached'])
        self.assertTrue(compare(self.a,self.b,self.root/'out')['cached'])
    def test_rgb_difference_is_detected_even_when_alpha_matches(self):
        with Image.open(self.b) as image:image.putpixel((1,1),(0,255,255,255));image.save(self.b)
        result=compare(self.a,self.b,self.root/'out');self.assertFalse(result['pixel_identical']);self.assertEqual(result['pixels_different'],1);self.assertEqual(result['difference_bounds'],(1,1,2,2))
    def test_dimensions_are_not_silently_resized(self):
        Image.new('RGBA',(4,3)).save(self.b)
        with self.assertRaisesRegex(ValueError,'dimensions differ'):compare(self.a,self.b,self.root/'out')
    def test_input_is_never_overwritten_and_corrupt_cache_is_rebuilt(self):
        with self.assertRaises(ValueError):compare(self.root/'overlay.png',self.b,self.root)
        compare(self.a,self.b,self.root/'out');(self.root/'out/diff.png').write_bytes(b'bad')
        self.assertFalse(compare(self.a,self.b,self.root/'out')['cached'])
