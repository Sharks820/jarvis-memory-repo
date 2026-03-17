"""generate_geometry.py -- headless Blender script for procedural geometry.

Usage (invoked by BlenderTool):
    blender --background --python generate_geometry.py -- <input_path> <output_path> geometry_type=<type> [dimensions=<w>x<h>x<d>]

Supported geometry types:
  cube / box     : bpy.ops.mesh.primitive_cube_add with dimensions
  plane / terrain: subdivided plane with noise displacement
  cylinder / pillar: cylinder with configurable segments

Exports result as FBX to output_path.

NOTE: This script runs inside Blender's embedded Python (bpy), NOT the Jarvis venv.
"""
import sys

import bpy  # noqa: PLC0415 -- blender-only import


def parse_args() -> dict[str, str]:
    """Parse arguments passed after '--' separator."""
    args: dict[str, str] = {}
    try:
        sep_idx = sys.argv.index("--")
    except ValueError:
        return args

    positional = []
    for token in sys.argv[sep_idx + 1 :]:
        if "=" in token:
            k, _, v = token.partition("=")
            args[k] = v
        else:
            positional.append(token)

    if len(positional) > 0:
        args["input_path"] = positional[0]
    if len(positional) > 1:
        args["output_path"] = positional[1]

    return args


def parse_dimensions(dim_str: str) -> tuple[float, float, float]:
    """Parse 'WxHxD' or 'W' dimension string into (x, y, z) floats."""
    parts = dim_str.lower().split("x")
    if len(parts) == 1:
        v = float(parts[0])
        return (v, v, v)
    elif len(parts) == 2:
        return (float(parts[0]), float(parts[1]), float(parts[0]))
    else:
        return (float(parts[0]), float(parts[1]), float(parts[2]))


def create_cube(dimensions: tuple[float, float, float]) -> None:
    """Create a cube primitive with the given dimensions."""
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.scale = dimensions
    bpy.ops.object.transform_apply(scale=True)


def create_terrain(dimensions: tuple[float, float, float]) -> None:
    """Create a subdivided plane with noise displacement (simple terrain)."""
    bpy.ops.mesh.primitive_plane_add(size=dimensions[0])
    obj = bpy.context.active_object

    # Subdivide for terrain detail
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.subdivide(number_cuts=16)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Add displacement using a Displace modifier with cloud texture
    tex = bpy.data.textures.new("TerrainNoise", type="CLOUDS")
    tex.noise_scale = 1.5

    mod = obj.modifiers.new(name="Terrain", type="DISPLACE")
    mod.texture = tex
    mod.strength = dimensions[2] if len(dimensions) == 3 else 1.0  # type: ignore[misc]
    bpy.ops.object.modifier_apply(modifier=mod.name)


def create_cylinder(dimensions: tuple[float, float, float], segments: int = 32) -> None:
    """Create a cylinder (pillar) with configurable height and radius."""
    radius = dimensions[0] / 2.0
    depth = dimensions[2]
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=segments,
        radius=radius,
        depth=depth,
    )


def create_geometry(geometry_type: str, dimensions: tuple[float, float, float]) -> None:
    """Dispatch to the appropriate geometry creator."""
    gtype = geometry_type.lower()
    if gtype in ("cube", "box"):
        create_cube(dimensions)
    elif gtype in ("plane", "terrain"):
        create_terrain(dimensions)
    elif gtype in ("cylinder", "pillar"):
        create_cylinder(dimensions)
    else:
        raise ValueError(f"Unknown geometry_type: {geometry_type!r}")


def export_fbx(output_path: str) -> None:
    """Export the scene as FBX."""
    bpy.ops.export_scene.fbx(filepath=output_path, use_selection=False, global_scale=1.0)


def main() -> None:
    args = parse_args()
    output_path = args.get("output_path", "")
    geometry_type = args.get("geometry_type", "cube")
    dim_str = args.get("dimensions", "2x2x2")

    if not output_path:
        print("ERROR: output_path is required", file=sys.stderr)
        sys.exit(1)

    dimensions = parse_dimensions(dim_str)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    create_geometry(geometry_type, dimensions)
    export_fbx(output_path)
    print(f"Exported {geometry_type} geometry ({dimensions}) to: {output_path}")


if __name__ == "__main__":
    main()
