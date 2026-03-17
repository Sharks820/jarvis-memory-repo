"""blender_scripts -- parameterized bpy scripts for headless Blender operations.

These scripts run *inside* Blender's embedded Python interpreter (bpy), not in
the Jarvis virtualenv.  They are invoked by BlenderTool via:

    blender --background --python <script.py> -- <args...>

Available scripts:
- optimize_mesh.py  : decimate + normal recalc + UV unwrap + FBX export
- generate_lod.py   : 3-level LOD chain (100% / 50% / 25%)
- generate_geometry.py: procedural architecture / terrain geometry
"""
