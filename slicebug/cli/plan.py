import argparse
import json
import math
import os.path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass

from slicebug.cricut.path_util_plugin import PathUtilPlugin
from slicebug.cricut.tools import TOOLS_BY_NAME
from slicebug.plan.plan import (
    PlanMaterial,
    PlanMat,
    PlanPath,
    PlanPathStep,
    Plan,
    PLAN_DPI,
)
from slicebug.exceptions import ProtocolError, UserError

SVG_NS_PREFIX = "{http://www.w3.org/2000/svg}"


def in_to_mm(x):
    return x * 25.4


MAT_FIT_EPSILON = 1e-6

MAT_PRESETS = {
    # Cricut Joy adhesive mats.
    "joy-standard": (4.5, 12.0),
    "joy-standard-long": (4.5, 12.0),
    "joy-standard-short": (4.5, 6.5),
    # Cricut Joy card mat. This currently selects dimensions only; card-mat
    # specific machine behavior is not implemented separately.
    "joy-card": (4.5, 6.25),
    # Common full-size Cricut mats for Maker/Explore-class machines.
    "maker-standard": (12.0, 12.0),
    "maker-long": (12.0, 24.0),
}


def parse_mat_size(string):
    normalized = string.lower().replace("×", "x").replace(",", "x")
    parts = normalized.split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"Invalid mat size {string}. Use WIDTHxHEIGHT in inches, "
            "for example 4.5x12."
        )

    try:
        width, height = (float(part.strip()) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid mat size {string}. Width and height must be numbers."
        )

    if not (math.isfinite(width) and math.isfinite(height)):
        raise argparse.ArgumentTypeError(
            "Mat width and height must be finite numbers."
        )

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Mat width and height must be positive.")

    return width, height


def resolve_mat(mat_preset, mat_size):
    if mat_size is not None:
        width, height = mat_size
    else:
        width, height = MAT_PRESETS[mat_preset]

    return PlanMat(width=width, height=height)


def material_fits_mat(material_width, material_height, mat):
    return (
        material_width <= mat.width + MAT_FIT_EPSILON
        and material_height <= mat.height + MAT_FIT_EPSILON
    )


def matrix_transpose(a):
    return [[a[j][i] for j in range(len(a))] for i in range(len(a[0]))]


def matrix_mul(a, b):
    bt = matrix_transpose(b)

    result = []
    for a_row in a:
        result.append([])
        for b_col in bt:
            assert len(a_row) == len(b_col)
            result[-1].append(sum(x * y for x, y in zip(a_row, b_col)))
    return result


@dataclass
class Transform:
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    @classmethod
    def from_matrix(cls, matrix):
        ((a, c, e), (b, d, f), (ex0, ex0_1, ex1)) = matrix
        assert ex0 == 0
        assert ex0_1 == 0
        assert ex1 == 1
        return cls(a, b, c, d, e, f)

    def matrix(self):
        return [[self.a, self.c, self.e], [self.b, self.d, self.f], [0, 0, 1]]

    def apply(self, x, y):
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def compose(self, other):
        return Transform.from_matrix(matrix_mul(self.matrix(), other.matrix()))


def parse_color_tool_mapping(string):
    map_re = r"^#?([\dA-Fa-f]{6}):([a-z_]+)$"
    match = re.match(map_re, string)

    if match is None:
        raise argparse.ArgumentTypeError(
            f"Invalid mapping {string}. Must be a string like FF0000:scoring_stylus."
        )

    color, tool = match.groups()

    if not color.startswith("#"):
        color = f"#{color}"

    if tool not in TOOLS_BY_NAME:
        raise argparse.ArgumentTypeError(
            f"Invalid tool name {tool}. Try `slicebug list-tools` to get a list of available tools."
        )

    return (color.lower(), TOOLS_BY_NAME[tool])


def plan_register_args(subparsers):
    parser = subparsers.add_parser("plan", help="Convert an SVG drawing to a cut plan.")
    parser.add_argument(
        "input_file", type=argparse.FileType("r"), help="Path to the SVG file."
    )
    parser.add_argument(
        "output_file",
        type=argparse.FileType("w"),
        help="Path where the plan will be saved.",
    )
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        type=parse_color_tool_mapping,
        help="Map a stroke color to a cutting tool. For example, 000000:fine_point_knife will cut along black lines, while ff0000:pen will draw along red lines.",
    )
    parser.add_argument(
        "--material",
        type=int,
        required=True,
        help="ID of the material being cut (see list-materials).",
    )
    parser.add_argument(
        "--mat-preset",
        choices=sorted(MAT_PRESETS),
        default="joy-standard",
        help=(
            "Mat preset. Defaults to joy-standard (4.5x12). Use --mat-size "
            "to provide custom dimensions. The joy-card preset selects the "
            "card mat dimensions only; card-mat-specific machine behavior is "
            "not implemented separately."
        ),
    )
    parser.add_argument(
        "--mat-size",
        type=parse_mat_size,
        metavar="WIDTHxHEIGHT",
        help=(
            "Override mat dimensions in inches, for example 4.5x12 or "
            "12x24. Unicode × and comma separators are also accepted."
        ),
    )
    parser.add_argument(
        "--reject-oversize",
        action="store_true",
        help=(
            "Reject SVGs that are larger than the selected mat. By default, "
            "oversize plans are still generated with a warning for backwards "
            "compatibility."
        ),
    )

    parser.set_defaults(cmd_handler=plan)
    parser.set_defaults(cmd_needs_profile=True)
    parser.set_defaults(cmd_needs_keys=False)


def run_usvg(usvg_path, input_svg):
    with tempfile.TemporaryDirectory(prefix="slicebug") as tempdir:
        input_path = os.path.join(tempdir, "input.svg")
        output_path = os.path.join(tempdir, "output.svg")

        with open(input_path, "w") as input_file:
            input_file.write(input_svg)

        result = subprocess.run(
            (usvg_path, input_path, output_path), capture_output=True, text=True
        )

        if result.returncode != 0:
            error_details = result.stderr
            raise UserError(
                f"Cannot read SVG. usvg says:\n{result.stderr}",
                "Make sure you're using the right file.",
            )

        with open(output_path) as output_file:
            return output_file.read()


def parse_simplified_svg(tree):
    width = float(tree.attrib["width"])
    height = float(tree.attrib["height"])
    svg_dpi = 96  # hard-coded usvg setting

    width_in = width / svg_dpi
    height_in = height / svg_dpi

    plan_width = width_in * PLAN_DPI
    plan_height = height_in * PLAN_DPI

    if tree.attrib.get("preserveAspectRatio", "none") != "none":
        raise UserError(
            "SVG document has preserveAspectRatio set to a value other than `none`. preserveAspectRatio is not supported.",
            "Modify the SVG document and try again.",
        )

    vb_min_x, vb_min_y, vb_width, vb_height = (
        float(x) for x in tree.attrib["viewBox"].split()
    )
    root_transform = Transform.from_matrix(
        [
            [plan_width / vb_width, 0, -vb_min_x * plan_width / vb_width],
            [0, plan_height / vb_height, -vb_min_y * plan_height / vb_height],
            [0, 0, 1],
        ]
    )

    paths = []
    paths_without_stroke = 0

    def extract_paths(element, transform):
        if "transform" in element.attrib:
            transform_def = element.attrib["transform"]
            assert transform_def.startswith("matrix(") and transform_def.endswith(
                ")"
            ), f"Invalid transform {transform_def}."
            transform_params = [
                float(x) for x in transform_def[len("matrix(") : -len(")")].split()
            ]
            transform = transform.compose(Transform(*transform_params))

        assert element.tag.startswith(
            SVG_NS_PREFIX
        ), f"Non-SVG tag {element.tag} in simplified SVG."
        tag = element.tag[len(SVG_NS_PREFIX) :]

        if tag == "path":
            nonlocal paths
            nonlocal paths_without_stroke

            stroke_color = element.attrib["stroke"].lower()
            if stroke_color == "none":
                paths_without_stroke += 1
                return

            # TODO: warn about paths that are partially or fully outside of
            # the viewbox, as that will cause the machine to behave
            # erratically.

            path_data = element.attrib["d"]
            path_steps = PlanPathStep.many_from_svg(path_data)
            for step in path_steps:
                step.points = [transform.apply(*point) for point in step.points]

            paths.append((stroke_color, path_steps))
        elif tag == "g" or tag == "svg":
            for child in element:
                extract_paths(child, transform)
        elif tag == "defs":
            pass
        elif tag == "image":
            print(
                "Warning: skipping embedded raster image. Raster images are not supported."
            )
        else:
            assert False, f"Unexpected tag {tag} in simplified SVG."

    extract_paths(tree, root_transform)

    if paths_without_stroke > 0:
        print(f"Warning: skipping {paths_without_stroke} paths with no stroke.")

    return width_in, height_in, paths


def plan(args, config):
    if config.usvg_path() is None:
        raise UserError("usvg is missing.", "Try running `slicebug bootstrap`.")

    simplified_svg = run_usvg(config.usvg_path(), args.input_file.read())
    svg_tree = ET.fromstring(simplified_svg)

    stroke_to_tool = dict(args.map)

    material_width, material_height, parsed_paths = parse_simplified_svg(svg_tree)
    material_width_mm = in_to_mm(material_width)
    material_height_mm = in_to_mm(material_height)
    print(
        f"Dimensions are {material_width:.1f} x {material_height:.1f} in "
        f"({material_width_mm:.0f} x {material_height_mm:.0f} mm)."
    )

    stroke_stats = Counter(stroke for stroke, _ in parsed_paths)

    print(f"Found {len(parsed_paths)} paths:")
    for stroke, path_count in sorted(stroke_stats.items()):
        tool = stroke_to_tool.get(stroke)
        if tool is not None:
            mapped = f"mapped to {tool.name}"
        else:
            mapped = "not mapped to any tool"

        print(f" - {path_count} paths with stroke color {stroke}, {mapped}")

    mat = resolve_mat(args.mat_preset, args.mat_size)
    if not material_fits_mat(material_width, material_height, mat):
        message = (
            f"SVG is {material_width:.1f} x {material_height:.1f} in, "
            f"which does not fit on the {mat.width:g} x {mat.height:g} in mat."
        )
        suggestion = (
            "Choose a larger mat with --mat-preset/--mat-size, scale the SVG "
            "down, or rotate the artwork in the SVG."
        )
        if args.reject_oversize:
            raise UserError(message, suggestion)
        print(f"Warning: {message}")
        print(suggestion)
    mat_description = args.mat_preset
    if args.mat_size is not None:
        mat_description = "custom"
    print(f"Using {mat_description} mat: {mat.width:g} x {mat.height:g} in.")
    material = PlanMaterial(
        width=material_width,
        height=material_height,
        cricut_api_global_id=args.material,
    )

    paths = []
    for stroke, steps in parsed_paths:
        tool = stroke_to_tool.get(stroke)
        if tool is None:
            continue

        paths.append(
            PlanPath(
                tool=tool,
                steps=steps,
                color=stroke if tool.name == "pen" else None,
            )
        )

    plan = Plan(
        mat=mat,
        material=material,
        paths=paths,
    )

    json.dump(plan.to_json(), args.output_file, indent=4)
