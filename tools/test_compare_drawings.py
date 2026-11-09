"""Structural checks for the reproducible drawing comparison."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('compare_drawings', Path(__file__).with_name('compare_drawings.py'))
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)


class Pixels:
    def __init__(self, size, data):
        self.size, self.data = size, data

    def convert(self, mode):
        assert mode == 'RGBA'
        return self

    def tobytes(self):
        return self.data


class DrawingComparisonTests(unittest.TestCase):
    def test_rgba_counts_pixels_including_hidden_colour(self):
        a = Pixels((2, 1), bytes([0, 0, 0, 0, 0, 0, 0, 255]))
        b = Pixels((2, 1), bytes([1, 0, 0, 0, 0, 0, 0, 255]))
        self.assertEqual(COMPARE.pixel_difference(a, b), {'different_pixels': 1})
        self.assertIsNone(COMPARE.pixel_difference(a, a))

    def test_page_size_is_compared(self):
        self.assertEqual(COMPARE.pixel_difference(Pixels((1, 2), b''), Pixels((2, 1), b'')),
                         {'before_size': (1, 2), 'after_size': (2, 1)})
