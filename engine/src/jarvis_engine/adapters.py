"""Backward-compat shim -- moved to jarvis_engine.media.adapters."""
from jarvis_engine.media.adapters import (  # noqa: F401
    AdapterBase,
    AdapterResult,
    ImageAdapter,
    Model3DAdapter,
    TaskType,
    VideoAdapter,
    _build_cube_obj,
    _build_cylinder_obj,
    _build_mesh_obj,
    _build_sphere_obj,
    _image_quality_size,
    _is_portrait_prompt,
    _mesh_kind,
    _timestamped_name,
    _video_profile,
)
