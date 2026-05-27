import argparse
import tempfile
import unittest

from slicebug.cli.plan import (
    MAT_PRESETS,
    material_fits_mat,
    parse_mat_size,
    plan_register_args,
    resolve_mat,
)
from slicebug.plan.plan import PlanMat


class PlanMatOptionsTest(unittest.TestCase):
    def test_parse_mat_size_accepts_common_separators(self):
        self.assertEqual(parse_mat_size("4.5x12"), (4.5, 12.0))
        self.assertEqual(parse_mat_size("4.5×12"), (4.5, 12.0))
        self.assertEqual(parse_mat_size("4.5,12"), (4.5, 12.0))

    def test_parse_mat_size_rejects_invalid_values(self):
        for value in ["4.5", "abc", "0x12", "4.5x0", "nanx12", "infx12"]:
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_mat_size(value)

    def test_resolve_mat_uses_named_preset(self):
        mat = resolve_mat("joy-card", None)
        self.assertEqual(mat, PlanMat(width=4.5, height=6.25))

    def test_resolve_mat_size_overrides_preset(self):
        mat = resolve_mat("joy-card", (12.0, 24.0))
        self.assertEqual(mat, PlanMat(width=12.0, height=24.0))

    def test_common_machine_presets_are_available(self):
        self.assertEqual(MAT_PRESETS["joy-standard"], (4.5, 12.0))
        self.assertEqual(MAT_PRESETS["joy-card"], (4.5, 6.25))
        self.assertEqual(MAT_PRESETS["maker-standard"], (12.0, 12.0))
        self.assertEqual(MAT_PRESETS["maker-long"], (12.0, 24.0))

    def test_material_fit_check_allows_tiny_floating_point_noise(self):
        mat = PlanMat(width=4.5, height=12.0)
        self.assertTrue(material_fits_mat(4.5000000001, 12.0000000001, mat))
        self.assertFalse(material_fits_mat(4.50001, 12.0, mat))

    def test_argparse_exposes_mat_options(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        plan_register_args(subparsers)

        with tempfile.NamedTemporaryFile("r", suffix=".svg") as input_file:
            with tempfile.NamedTemporaryFile("w", suffix=".json") as output_file:
                args = parser.parse_args(
                    [
                        "plan",
                        input_file.name,
                        output_file.name,
                        "--material",
                        "218",
                        "--mat-preset",
                        "maker-long",
                        "--mat-size",
                        "12x24",
                        "--reject-oversize",
                    ]
                )

        self.assertEqual(args.mat_preset, "maker-long")
        self.assertEqual(args.mat_size, (12.0, 24.0))
        self.assertTrue(args.reject_oversize)
        args.input_file.close()
        args.output_file.close()


if __name__ == "__main__":
    unittest.main()
