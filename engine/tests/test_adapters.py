"""Tests for the media adapters module (Image/Video/3D)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.adapters import (
    AdapterBase,
    AdapterResult,
    ImageAdapter,
    Model3DAdapter,
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


# ---------------------------------------------------------------------------
# AdapterResult dataclass
# ---------------------------------------------------------------------------


class TestAdapterResult:
    def test_defaults(self):
        r = AdapterResult(ok=True, provider="p", plan="pl", reason="r")
        assert r.output_path == ""
        assert r.output_text == ""

    def test_all_fields(self):
        r = AdapterResult(
            ok=False, provider="x", plan="y", reason="z",
            output_path="/tmp/out.png", output_text="done",
        )
        assert r.ok is False
        assert r.output_path == "/tmp/out.png"


# ---------------------------------------------------------------------------
# AdapterBase
# ---------------------------------------------------------------------------


class TestAdapterBase:
    def test_plan_raises(self):
        with pytest.raises(NotImplementedError):
            AdapterBase().plan("test")

    def test_execute_raises(self):
        with pytest.raises(NotImplementedError):
            AdapterBase().execute("test", None, "balanced")


# ---------------------------------------------------------------------------
# Helper / utility functions
# ---------------------------------------------------------------------------


class TestTimestampedName:
    def test_format(self):
        name = _timestamped_name("img", ".png")
        assert name.startswith("img-")
        assert name.endswith(".png")
        # Should contain date portion: YYYYMMDD
        parts = name.replace("img-", "").replace(".png", "")
        assert len(parts) > 8  # timestamp string

    def test_different_prefix_suffix(self):
        name = _timestamped_name("video", ".mp4")
        assert name.startswith("video-")
        assert name.endswith(".mp4")


class TestIsPortraitPrompt:
    @pytest.mark.parametrize("term", [
        "portrait of a person",
        "phone wallpaper landscape",
        "a vertical banner",
        "make a tiktok video",
        "instagram reel style",
        "my story background",
    ])
    def test_portrait_keywords(self, term):
        assert _is_portrait_prompt(term) is True

    def test_non_portrait(self):
        assert _is_portrait_prompt("a landscape painting of mountains") is False

    def test_case_insensitive(self):
        assert _is_portrait_prompt("PORTRAIT of a king") is True

    def test_empty_string(self):
        assert _is_portrait_prompt("") is False


class TestImageQualitySize:
    def test_max_quality_landscape(self):
        quality, size = _image_quality_size("a castle", "max_quality")
        assert quality == "high"
        assert size == "1536x1024"

    def test_max_quality_portrait(self):
        quality, size = _image_quality_size("a portrait painting", "max_quality")
        assert quality == "high"
        assert size == "1024x1536"

    def test_balanced(self):
        quality, size = _image_quality_size("anything", "balanced")
        assert quality == "medium"
        assert size == "1024x1024"

    def test_low_default(self):
        quality, size = _image_quality_size("anything", "fast")
        assert quality == "low"
        assert size == "1024x1024"


class TestVideoProfile:
    def test_max_quality_landscape(self):
        model, secs, size = _video_profile("a castle cinematic", "max_quality")
        assert model == "sora-2-pro"
        assert secs == "12"
        assert size == "1792x1024"

    def test_max_quality_portrait(self):
        model, secs, size = _video_profile("tiktok dance", "max_quality")
        assert size == "1024x1792"

    def test_balanced_landscape(self):
        model, secs, size = _video_profile("a castle", "balanced")
        assert model == "sora-2"
        assert secs == "8"
        assert size == "1280x720"

    def test_balanced_portrait(self):
        _, _, size = _video_profile("vertical clip", "balanced")
        assert size == "720x1280"

    def test_fast(self):
        model, secs, size = _video_profile("test", "fast")
        assert model == "sora-2"
        assert secs == "4"
        assert size == "1280x720"


class TestMeshKind:
    @pytest.mark.parametrize("prompt,expected", [
        ("a sphere object", "sphere"),
        ("planet earth", "sphere"),
        ("an orb of light", "sphere"),
        ("a rubber ball", "sphere"),
        ("a cylinder tube", "cylinder"),
        ("water pipe", "cylinder"),
        ("wooden barrel", "cylinder"),
        ("stone tower", "cylinder"),
        ("marble column", "cylinder"),
        ("a house", "cube"),
        ("random thing", "cube"),
        ("", "cube"),
    ])
    def test_detection(self, prompt, expected):
        assert _mesh_kind(prompt) == expected


# ---------------------------------------------------------------------------
# Mesh builders
# ---------------------------------------------------------------------------


class TestBuildCubeObj:
    def test_vertex_face_counts(self):
        obj, verts, faces = _build_cube_obj("test cube")
        assert verts == 8
        assert faces == 6

    def test_contains_header(self):
        obj, _, _ = _build_cube_obj("my cube")
        assert "# Generated by Jarvis" in obj
        assert "# prompt: my cube" in obj
        assert "o jarvis_cube" in obj

    def test_newlines_in_prompt_sanitised(self):
        obj, _, _ = _build_cube_obj("line1\nline2\rline3")
        assert "\n# prompt: line1 line2 line3\n" in obj


class TestBuildSphereObj:
    def test_counts_low_quality(self):
        obj, verts, faces = _build_sphere_obj("test", rings=10, segments=20)
        # 2 poles + (rings-1)*segments intermediate vertices
        expected_verts = 2 + (10 - 1) * 20
        assert verts == expected_verts
        # top fan = segments, bottom fan = segments, middle strips = (rings-2)*segments*2
        expected_faces = 20 + 20 + (10 - 2) * 20 * 2
        assert faces == expected_faces

    def test_contains_sphere_header(self):
        obj, _, _ = _build_sphere_obj("planet", rings=4, segments=8)
        assert "o jarvis_sphere" in obj

    def test_min_rings(self):
        # Even with rings=2, should still produce valid output
        obj, verts, faces = _build_sphere_obj("tiny", rings=2, segments=4)
        assert verts > 0
        assert faces > 0


class TestBuildCylinderObj:
    def test_counts(self):
        obj, verts, faces = _build_cylinder_obj("pipe", segments=12)
        # 2 centers + segments * 2 (top/bottom ring)
        expected_verts = 2 + 12 * 2
        assert verts == expected_verts
        # per segment: top cap + bottom cap + 2 side triangles = 4
        expected_faces = 12 * 4
        assert faces == expected_faces

    def test_contains_cylinder_header(self):
        obj, _, _ = _build_cylinder_obj("barrel", segments=6)
        assert "o jarvis_cylinder" in obj


class TestBuildMeshObj:
    """Tests for the dispatcher _build_mesh_obj."""

    def test_sphere_max_quality(self):
        _, verts, _ = _build_mesh_obj("ball", "max_quality", "sphere")
        # rings=24, segments=48 -> 2 + 23*48
        assert verts == 2 + 23 * 48

    def test_sphere_balanced(self):
        _, verts, _ = _build_mesh_obj("ball", "balanced", "sphere")
        assert verts == 2 + 15 * 32

    def test_sphere_fast(self):
        _, verts, _ = _build_mesh_obj("ball", "fast", "sphere")
        assert verts == 2 + 9 * 20

    def test_cylinder_max(self):
        _, verts, _ = _build_mesh_obj("pipe", "max_quality", "cylinder")
        assert verts == 2 + 48 * 2

    def test_cylinder_balanced(self):
        _, verts, _ = _build_mesh_obj("pipe", "balanced", "cylinder")
        assert verts == 2 + 24 * 2

    def test_cylinder_fast(self):
        _, verts, _ = _build_mesh_obj("pipe", "fast", "cylinder")
        assert verts == 2 + 12 * 2

    def test_cube_fallback(self):
        _, verts, faces = _build_mesh_obj("house", "max_quality", "cube")
        assert verts == 8
        assert faces == 6


# ---------------------------------------------------------------------------
# ImageAdapter
# ---------------------------------------------------------------------------


class TestImageAdapter:
    def _make(self, repo_root: Path | None = None) -> ImageAdapter:
        return ImageAdapter(repo_root or Path("/fake/repo"))

    def test_plan(self):
        adapter = self._make()
        plan = adapter.plan("a dragon")
        assert "image" in plan.lower() or "image_gen" in plan.lower()
        assert "a dragon" in plan

    def test_task_type(self):
        assert self._make().task_type == "image"

    def test_provider(self):
        assert self._make().provider == "openai_image_api"

    def test_execute_missing_script(self):
        adapter = self._make()
        adapter.script = Path("/nonexistent/script.py")
        result = adapter.execute("cat", None, "balanced")
        assert result.ok is False
        assert "Missing" in result.reason

    @patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False)
    def test_execute_no_api_key(self, tmp_path):
        adapter = self._make(tmp_path)
        # Create a fake script so the first check passes
        script = tmp_path / "fake_script.py"
        script.write_text("pass")
        adapter.script = script
        result = adapter.execute("cat", None, "balanced")
        assert result.ok is False
        assert "OPENAI_API_KEY" in result.reason

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_success(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "fake_script.py"
        script.write_text("pass")
        adapter.script = script

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        out_file = str(tmp_path / "out.png")
        result = adapter.execute("a cat", out_file, "balanced")
        assert result.ok is True
        assert "completed" in result.reason.lower()
        mock_run.assert_called_once()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_process_failure(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "fake_script.py"
        script.write_text("pass")
        adapter.script = script

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        out_file = str(tmp_path / "out.png")
        result = adapter.execute("a cat", out_file, "balanced")
        assert result.ok is False
        assert "boom" in result.reason

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=300))
    def test_execute_timeout(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "fake_script.py"
        script.write_text("pass")
        adapter.script = script

        result = adapter.execute("a cat", str(tmp_path / "out.png"), "balanced")
        assert result.ok is False
        assert "timed out" in result.reason.lower()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_max_quality_timeout(self, mock_run, tmp_path):
        """max_quality uses 600s timeout instead of 300s."""
        adapter = self._make(tmp_path)
        script = tmp_path / "fake_script.py"
        script.write_text("pass")
        adapter.script = script

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        adapter.execute("a cat", str(tmp_path / "out.png"), "max_quality")
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 600

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_default_output_path(self, mock_run, tmp_path):
        """When output_path is None, a default is generated under repo_root/output/imagegen/."""
        adapter = self._make(tmp_path)
        script = tmp_path / "fake_script.py"
        script.write_text("pass")
        adapter.script = script
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = adapter.execute("cat picture", None, "balanced")
        assert result.ok is True
        assert "imagegen" in result.output_path.replace("\\", "/")


# ---------------------------------------------------------------------------
# VideoAdapter
# ---------------------------------------------------------------------------


class TestVideoAdapter:
    def _make(self, repo_root: Path | None = None) -> VideoAdapter:
        return VideoAdapter(repo_root or Path("/fake/repo"))

    def test_plan(self):
        plan = self._make().plan("flying car")
        assert "flying car" in plan
        assert "video" in plan.lower() or "Sora" in plan

    def test_task_type(self):
        assert self._make().task_type == "video"

    def test_provider(self):
        assert self._make().provider == "openai_sora_api"

    def test_execute_missing_script(self):
        adapter = self._make()
        adapter.script = Path("/nonexistent/sora.py")
        result = adapter.execute("cat", None, "balanced")
        assert result.ok is False
        assert "Missing" in result.reason

    @patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False)
    def test_execute_no_api_key(self, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "sora.py"
        script.write_text("pass")
        adapter.script = script
        result = adapter.execute("cat", None, "balanced")
        assert result.ok is False
        assert "OPENAI_API_KEY" in result.reason

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_success(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "sora.py"
        script.write_text("pass")
        adapter.script = script
        mock_run.return_value = MagicMock(returncode=0, stdout="video done", stderr="")
        result = adapter.execute("flying car", str(tmp_path / "vid.mp4"), "balanced")
        assert result.ok is True
        assert "completed" in result.reason.lower()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_failure(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "sora.py"
        script.write_text("pass")
        adapter.script = script
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="sora error")
        result = adapter.execute("car", str(tmp_path / "vid.mp4"), "balanced")
        assert result.ok is False
        assert "sora error" in result.reason

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1200))
    def test_execute_timeout(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "sora.py"
        script.write_text("pass")
        adapter.script = script
        result = adapter.execute("car", str(tmp_path / "vid.mp4"), "balanced")
        assert result.ok is False
        assert "timed out" in result.reason.lower()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_max_quality_timeout(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "sora.py"
        script.write_text("pass")
        adapter.script = script
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        adapter.execute("car", str(tmp_path / "vid.mp4"), "max_quality")
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 2100

    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False)
    @patch("jarvis_engine.adapters.subprocess.run")
    def test_execute_default_output_path(self, mock_run, tmp_path):
        adapter = self._make(tmp_path)
        script = tmp_path / "sora.py"
        script.write_text("pass")
        adapter.script = script
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = adapter.execute("car", None, "balanced")
        assert result.ok is True
        assert "video" in result.output_path.replace("\\", "/")


# ---------------------------------------------------------------------------
# Model3DAdapter
# ---------------------------------------------------------------------------


class TestModel3DAdapter:
    def _make(self, repo_root: Path | None = None) -> Model3DAdapter:
        return Model3DAdapter(repo_root or Path("/fake/repo"))

    def test_plan(self):
        plan = self._make().plan("a castle")
        assert "a castle" in plan
        assert "3D" in plan or "OBJ" in plan

    def test_task_type(self):
        assert self._make().task_type == "model3d"

    def test_provider(self):
        assert self._make().provider == "local_mesh_generator"

    def test_execute_cube(self, tmp_path):
        adapter = self._make(tmp_path)
        out = str(tmp_path / "out.obj")
        result = adapter.execute("a house", out, "balanced")
        assert result.ok is True
        assert "cube" in result.reason.lower()
        obj_text = Path(out).read_text()
        assert "jarvis_cube" in obj_text

    def test_execute_sphere(self, tmp_path):
        adapter = self._make(tmp_path)
        out = str(tmp_path / "out.obj")
        result = adapter.execute("a sphere object", out, "balanced")
        assert result.ok is True
        assert "sphere" in result.reason.lower()
        obj_text = Path(out).read_text()
        assert "jarvis_sphere" in obj_text

    def test_execute_cylinder(self, tmp_path):
        adapter = self._make(tmp_path)
        out = str(tmp_path / "out.obj")
        result = adapter.execute("a cylinder tube", out, "balanced")
        assert result.ok is True
        assert "cylinder" in result.reason.lower()

    def test_metadata_json_written(self, tmp_path):
        adapter = self._make(tmp_path)
        out = str(tmp_path / "model.obj")
        adapter.execute("a house", out, "balanced")
        meta = tmp_path / "model.json"
        assert meta.exists()
        import json
        data = json.loads(meta.read_text())
        assert data["prompt"] == "a house"
        assert data["generator"] == "local_mesh_generator"
        assert data["mesh_type"] == "cube"
        assert data["quality_profile"] == "balanced"
        assert data["vertices"] == 8
        assert data["faces"] == 6

    def test_execute_default_output_path(self, tmp_path):
        adapter = self._make(tmp_path)
        result = adapter.execute("a house", None, "balanced")
        assert result.ok is True
        assert "model3d" in result.output_path.replace("\\", "/")

    def test_execute_oserror_mkdir(self, tmp_path):
        adapter = self._make(tmp_path)
        with patch.object(Path, "mkdir", side_effect=OSError("no perms")):
            result = adapter.execute("a house", str(tmp_path / "sub" / "out.obj"), "balanced")
        assert result.ok is False
        assert "directory" in result.reason.lower()

    def test_execute_oserror_write(self, tmp_path):
        adapter = self._make(tmp_path)
        out = str(tmp_path / "out.obj")
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = adapter.execute("a house", out, "balanced")
        assert result.ok is False
        assert "write" in result.reason.lower() or "disk" in result.reason.lower()

    def test_metadata_write_failure_still_ok(self, tmp_path):
        """If metadata write fails, the result should still be ok (just a warning)."""
        adapter = self._make(tmp_path)
        out = str(tmp_path / "model.obj")

        original_write_text = Path.write_text

        def write_text_wrapper(self_path, data, *args, **kwargs):
            if str(self_path).endswith(".json"):
                raise OSError("meta write fail")
            return original_write_text(self_path, data, *args, **kwargs)

        # Use `new` instead of `side_effect` to preserve the self argument.
        with patch.object(Path, "write_text", new=write_text_wrapper):
            result = adapter.execute("a house", out, "balanced")
        assert result.ok is True
        # The .obj should still have been written
        assert Path(out).exists()


# ---------------------------------------------------------------------------
# Environment variable overrides for script paths
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    @patch.dict("os.environ", {"JARVIS_IMAGE_SCRIPT": "/custom/image_gen.py"}, clear=False)
    def test_image_script_override(self):
        adapter = ImageAdapter(Path("/repo"))
        assert adapter.script == Path("/custom/image_gen.py")

    @patch.dict("os.environ", {"JARVIS_SORA_SCRIPT": "/custom/sora.py"}, clear=False)
    def test_video_script_override(self):
        adapter = VideoAdapter(Path("/repo"))
        assert adapter.script == Path("/custom/sora.py")
