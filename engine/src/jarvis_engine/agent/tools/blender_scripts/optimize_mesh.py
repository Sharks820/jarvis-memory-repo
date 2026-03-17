"""optimize_mesh.py -- headless Blender script for mesh post-processing.

Usage (invoked by BlenderTool):
    blender --background --python optimize_mesh.py -- <input_path> <output_path> [decimate_ratio=0.5]

Operations performed:
1. Import FBX or GLB model from input_path
2. Apply Decimate modifier to reduce polygon count
3. Recalculate normals (outside facing)
4. Smart UV Project for texture coordinates
5. Export result as FBX to output_path

NOTE: This script runs inside Blender's embedded Python (bpy), NOT the Jarvis venv.
"""
import sys

# bpy is only available inside Blender's embedded Python interpreter.
import bpy  # noqa: PLC0415 -- blender-only import


def parse_args() -> dict[str, str]:
    """Parse arguments passed after '--' separator by BlenderTool."""
    args: dict[str, str] = {}
    # sys.argv contains: blender args ... "--" input_path output_path [key=value ...]
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


def import_model(path: str) -> None:
    """Import FBX or GLB/GLTF model."""
    lower = path.lower()
    if lower.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    elif lower.endswith(".glb") or lower.endswith(".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise ValueError(f"Unsupported input format: {path}")


def optimize_mesh(decimate_ratio: float = 0.5) -> None:
    """Apply decimate, recalculate normals, and UV unwrap all mesh objects."""
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue

        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        # -- Decimate modifier --
        mod = obj.modifiers.new(name="Decimate", type="DECIMATE")
        mod.ratio = decimate_ratio
        bpy.ops.object.modifier_apply(modifier=mod.name)

        # -- Recalculate normals --
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode="OBJECT")

        # -- Smart UV Project --
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project()
        bpy.ops.object.mode_set(mode="OBJECT")

        obj.select_set(False)


def export_fbx(output_path: str) -> None:
    """Export scene as FBX."""
    bpy.ops.export_scene.fbx(
        filepath=output_path,
        use_selection=False,
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_NONE",
    )


def main() -> None:
    args = parse_args()
    input_path = args.get("input_path", "")
    output_path = args.get("output_path", "")
    decimate_ratio = float(args.get("decimate_ratio", "0.5"))

    if not input_path or not output_path:
        print("ERROR: input_path and output_path are required", file=sys.stderr)
        sys.exit(1)

    # Clear default scene objects
    bpy.ops.wm.read_factory_settings(use_empty=True)

    import_model(input_path)
    optimize_mesh(decimate_ratio=decimate_ratio)
    export_fbx(output_path)
    print(f"Exported optimized mesh to: {output_path}")


if __name__ == "__main__":
    main()
