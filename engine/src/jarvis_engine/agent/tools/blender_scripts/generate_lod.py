"""generate_lod.py -- headless Blender script for LOD chain generation.

Usage (invoked by BlenderTool):
    blender --background --python generate_lod.py -- <input_path> <output_path> [lod_levels=3]

Generates N LOD copies of an imported mesh at decreasing polygon counts:
  LOD0: 100% (original)
  LOD1:  50% via Decimate modifier
  LOD2:  25% via Decimate modifier

Each LOD is exported as a separate FBX named:
  {basename}_LOD0.fbx, {basename}_LOD1.fbx, {basename}_LOD2.fbx

NOTE: This script runs inside Blender's embedded Python (bpy), NOT the Jarvis venv.
"""
import sys
from pathlib import Path

import bpy  # noqa: PLC0415 -- blender-only import


# Decimate ratios per LOD level (LOD0 = full resolution, no decimation)
_LOD_RATIOS = [1.0, 0.5, 0.25]


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


def import_model(path: str) -> None:
    """Import FBX or GLB/GLTF model."""
    lower = path.lower()
    if lower.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    elif lower.endswith(".glb") or lower.endswith(".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise ValueError(f"Unsupported input format: {path}")


def apply_decimate(obj: object, ratio: float) -> None:
    """Apply a Decimate modifier with the given ratio to obj."""
    bpy.context.view_layer.objects.active = obj  # type: ignore[arg-type]
    obj.select_set(True)  # type: ignore[attr-defined]
    if ratio < 1.0:
        mod = obj.modifiers.new(name="Decimate", type="DECIMATE")  # type: ignore[attr-defined]
        mod.ratio = ratio
        bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.select_set(False)  # type: ignore[attr-defined]


def export_lod(lod_index: int, base_path: str) -> str:
    """Export the current scene as FBX for a given LOD index."""
    stem = Path(base_path).stem
    parent = Path(base_path).parent
    out_path = str(parent / f"{stem}_LOD{lod_index}.fbx")
    bpy.ops.export_scene.fbx(filepath=out_path, use_selection=False, global_scale=1.0)
    return out_path


def main() -> None:
    args = parse_args()
    input_path = args.get("input_path", "")
    output_path = args.get("output_path", "")
    lod_levels = int(args.get("lod_levels", "3"))

    if not input_path or not output_path:
        print("ERROR: input_path and output_path are required", file=sys.stderr)
        sys.exit(1)

    lod_levels = max(1, min(lod_levels, len(_LOD_RATIOS)))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_model(input_path)

    # Collect all mesh objects from the imported scene
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]

    for lod_idx in range(lod_levels):
        ratio = _LOD_RATIOS[lod_idx]

        # Duplicate objects for this LOD level (skip for LOD0 -- keep originals)
        if lod_idx == 0:
            lod_objs = mesh_objects
        else:
            # Deselect all, select mesh objects, duplicate in-place
            bpy.ops.object.select_all(action="DESELECT")
            for obj in mesh_objects:
                obj.select_set(True)
            bpy.ops.object.duplicate(linked=False)
            lod_objs = list(bpy.context.selected_objects)

        for obj in lod_objs:
            apply_decimate(obj, ratio)

        out_path = export_lod(lod_idx, output_path)
        print(f"Exported LOD{lod_idx} (ratio={ratio}) to: {out_path}")

        # Remove duplicated objects after export (keep only originals)
        if lod_idx > 0:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in lod_objs:
                obj.select_set(True)
            bpy.ops.object.delete()
            bpy.ops.object.select_all(action="DESELECT")


if __name__ == "__main__":
    main()
