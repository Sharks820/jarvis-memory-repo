from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import Literal

TaskType = Literal["image", "video", "model3d"]


@dataclass
class AdapterResult:
    ok: bool
    provider: str
    plan: str
    reason: str
    output_path: str = ""
    output_text: str = ""


class AdapterBase:
    task_type: TaskType
    provider: str

    def plan(self, prompt: str) -> str:
        raise NotImplementedError

    def execute(self, prompt: str, output_path: str | None, quality_profile: str) -> AdapterResult:
        raise NotImplementedError


class ImageAdapter(AdapterBase):
    task_type: TaskType = "image"
    provider = "openai_image_api"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.script = Path(
            os.getenv(
                "JARVIS_IMAGE_SCRIPT",
                str(Path.home() / ".codex" / "skills" / "imagegen" / "scripts" / "image_gen.py"),
            )
        )

    def plan(self, prompt: str) -> str:
        return (
            "Generate image via bundled image CLI "
            f"({self.script.name}) using prompt: {prompt}"
        )

    def execute(self, prompt: str, output_path: str | None, quality_profile: str) -> AdapterResult:
        if not self.script.exists():
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason=f"Missing image adapter script: {self.script}",
            )
        if not os.getenv("OPENAI_API_KEY"):
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason="OPENAI_API_KEY not set.",
            )

        out = output_path or str(self.repo_root / "output" / "imagegen" / _timestamped_name("image", ".png"))
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        quality, size = _image_quality_size(prompt, quality_profile)
        cmd = [
            sys.executable,
            str(self.script),
            "generate",
            "--prompt",
            prompt,
            "--model",
            "gpt-image-1.5",
            "--quality",
            quality,
            "--size",
            size,
            "--style",
            "cinematic detailed concept art",
            "--out",
            str(out_path),
            "--force",
        ]
        timeout_s = 600 if quality_profile == "max_quality" else 300
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason=f"Image generation timed out after {timeout_s}s.",
            )
        if proc.returncode != 0:
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason=proc.stderr.strip() or "Image generation failed.",
            )
        return AdapterResult(
            ok=True,
            provider=self.provider,
            plan=self.plan(prompt),
            reason="Image generation completed.",
            output_path=str(out_path.resolve()),
            output_text=proc.stdout.strip(),
        )


class VideoAdapter(AdapterBase):
    task_type: TaskType = "video"
    provider = "openai_sora_api"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.script = Path(
            os.getenv(
                "JARVIS_SORA_SCRIPT",
                str(Path.home() / ".codex" / "skills" / "sora" / "scripts" / "sora.py"),
            )
        )

    def plan(self, prompt: str) -> str:
        return (
            "Generate video via bundled Sora CLI "
            f"({self.script.name}) using prompt: {prompt}"
        )

    def execute(self, prompt: str, output_path: str | None, quality_profile: str) -> AdapterResult:
        if not self.script.exists():
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason=f"Missing video adapter script: {self.script}",
            )
        if not os.getenv("OPENAI_API_KEY"):
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason="OPENAI_API_KEY not set.",
            )

        out = output_path or str(self.repo_root / "output" / "video" / _timestamped_name("video", ".mp4"))
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        json_out = out_path.with_suffix(".json")

        model, seconds, size = _video_profile(prompt, quality_profile)
        cmd = [
            sys.executable,
            str(self.script),
            "create-and-poll",
            "--model",
            model,
            "--prompt",
            prompt,
            "--seconds",
            seconds,
            "--size",
            size,
            "--style",
            "cinematic high fidelity",
            "--download",
            "--out",
            str(out_path),
            "--json-out",
            str(json_out),
        ]
        timeout_s = 2100 if quality_profile == "max_quality" else 1200
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason=f"Video generation timed out after {timeout_s}s.",
            )
        if proc.returncode != 0:
            return AdapterResult(
                ok=False,
                provider=self.provider,
                plan=self.plan(prompt),
                reason=proc.stderr.strip() or "Video generation failed.",
            )
        return AdapterResult(
            ok=True,
            provider=self.provider,
            plan=self.plan(prompt),
            reason="Video generation completed.",
            output_path=str(out_path.resolve()),
            output_text=proc.stdout.strip(),
        )


class Model3DAdapter(AdapterBase):
    task_type: TaskType = "model3d"
    provider = "local_mesh_generator"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def plan(self, prompt: str) -> str:
        return f"Generate a local starter OBJ mesh and metadata from prompt: {prompt}"

    def execute(self, prompt: str, output_path: str | None, quality_profile: str) -> AdapterResult:
        out = output_path or str(self.repo_root / "output" / "model3d" / _timestamped_name("model", ".obj"))
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        mesh_kind = _mesh_kind(prompt)
        obj, vertices, faces = _build_mesh_obj(prompt, quality_profile, mesh_kind)
        out_path.write_text(obj, encoding="utf-8")
        meta_path = out_path.with_suffix(".json")
        metadata = {
            "prompt": prompt,
            "generator": "local_mesh_generator",
            "mesh_type": mesh_kind,
            "quality_profile": quality_profile,
            "vertices": vertices,
            "faces": faces,
            "created_utc": datetime.now(UTC).isoformat(),
        }
        meta_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
        return AdapterResult(
            ok=True,
            provider=self.provider,
            plan=self.plan(prompt),
            reason=f"3D {mesh_kind} mesh generated locally.",
            output_path=str(out_path.resolve()),
            output_text=f"metadata={meta_path.resolve()}",
        )


def _timestamped_name(prefix: str, suffix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"{prefix}-{stamp}{suffix}"


def _is_portrait_prompt(prompt: str) -> bool:
    lowered = prompt.lower()
    portrait_terms = ["portrait", "phone wallpaper", "vertical", "tiktok", "reel", "story"]
    return any(term in lowered for term in portrait_terms)


def _image_quality_size(prompt: str, quality_profile: str) -> tuple[str, str]:
    if quality_profile == "max_quality":
        size = "1024x1536" if _is_portrait_prompt(prompt) else "1536x1024"
        return "high", size
    if quality_profile == "balanced":
        return "medium", "1024x1024"
    return "low", "1024x1024"


def _video_profile(prompt: str, quality_profile: str) -> tuple[str, str, str]:
    portrait = _is_portrait_prompt(prompt)
    if quality_profile == "max_quality":
        return "sora-2-pro", "12", ("1024x1792" if portrait else "1792x1024")
    if quality_profile == "balanced":
        return "sora-2", "8", ("720x1280" if portrait else "1280x720")
    return "sora-2", "4", ("720x1280" if portrait else "1280x720")


def _mesh_kind(prompt: str) -> str:
    lowered = prompt.lower()
    if any(term in lowered for term in ["sphere", "planet", "orb", "ball"]):
        return "sphere"
    if any(term in lowered for term in ["cylinder", "pipe", "barrel", "tower", "column"]):
        return "cylinder"
    return "cube"


def _build_mesh_obj(prompt: str, quality_profile: str, mesh_kind: str) -> tuple[str, int, int]:
    if mesh_kind == "sphere":
        if quality_profile == "max_quality":
            return _build_sphere_obj(prompt, rings=24, segments=48)
        if quality_profile == "balanced":
            return _build_sphere_obj(prompt, rings=16, segments=32)
        return _build_sphere_obj(prompt, rings=10, segments=20)
    if mesh_kind == "cylinder":
        if quality_profile == "max_quality":
            return _build_cylinder_obj(prompt, segments=48)
        if quality_profile == "balanced":
            return _build_cylinder_obj(prompt, segments=24)
        return _build_cylinder_obj(prompt, segments=12)
    return _build_cube_obj(prompt)


def _build_cube_obj(prompt: str) -> tuple[str, int, int]:
    comment = prompt.replace("\n", " ").replace("\r", " ")
    text = (
        f"# Generated by Jarvis local 3D adapter\n# prompt: {comment}\n"
        "o jarvis_cube\n"
        "v -0.5 -0.5 -0.5\n"
        "v 0.5 -0.5 -0.5\n"
        "v 0.5 0.5 -0.5\n"
        "v -0.5 0.5 -0.5\n"
        "v -0.5 -0.5 0.5\n"
        "v 0.5 -0.5 0.5\n"
        "v 0.5 0.5 0.5\n"
        "v -0.5 0.5 0.5\n"
        "f 1 2 3 4\n"
        "f 5 6 7 8\n"
        "f 1 5 8 4\n"
        "f 2 6 7 3\n"
        "f 4 3 7 8\n"
        "f 1 2 6 5\n"
    )
    return text, 8, 6


def _build_sphere_obj(prompt: str, rings: int, segments: int) -> tuple[str, int, int]:
    comment = prompt.replace("\n", " ").replace("\r", " ")
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    # Top pole.
    vertices.append((0.0, 0.5, 0.0))

    # Intermediate rings.
    for ring in range(1, rings):
        theta = math.pi * ring / rings
        y = 0.5 * math.cos(theta)
        radius = 0.5 * math.sin(theta)
        for seg in range(segments):
            phi = 2.0 * math.pi * seg / segments
            x = radius * math.cos(phi)
            z = radius * math.sin(phi)
            vertices.append((x, y, z))

    # Bottom pole.
    vertices.append((0.0, -0.5, 0.0))
    top_index = 1
    first_ring = 2
    last_ring = first_ring + (rings - 2) * segments
    bottom_index = len(vertices)

    # Top fan.
    for seg in range(segments):
        a = top_index
        b = first_ring + seg
        c = first_ring + ((seg + 1) % segments)
        faces.append((a, b, c))

    # Middle strips.
    for ring in range(rings - 2):
        curr = first_ring + ring * segments
        nxt = curr + segments
        for seg in range(segments):
            a = curr + seg
            b = curr + ((seg + 1) % segments)
            c = nxt + ((seg + 1) % segments)
            d = nxt + seg
            faces.append((a, d, c))
            faces.append((a, c, b))

    # Bottom fan.
    for seg in range(segments):
        a = bottom_index
        b = last_ring + ((seg + 1) % segments)
        c = last_ring + seg
        faces.append((a, b, c))

    lines = ["# Generated by Jarvis local 3D adapter", f"# prompt: {comment}", "o jarvis_sphere"]
    lines.extend([f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices])
    lines.extend([f"f {a} {b} {c}" for a, b, c in faces])
    return "\n".join(lines) + "\n", len(vertices), len(faces)


def _build_cylinder_obj(prompt: str, segments: int) -> tuple[str, int, int]:
    comment = prompt.replace("\n", " ").replace("\r", " ")
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    # Centers.
    vertices.append((0.0, 0.5, 0.0))
    vertices.append((0.0, -0.5, 0.0))
    top_center = 1
    bottom_center = 2

    top_ring_start = 3
    for seg in range(segments):
        angle = 2.0 * math.pi * seg / segments
        x = 0.5 * math.cos(angle)
        z = 0.5 * math.sin(angle)
        vertices.append((x, 0.5, z))
        vertices.append((x, -0.5, z))

    # Top and bottom caps.
    for seg in range(segments):
        top_a = top_ring_start + (seg * 2)
        top_b = top_ring_start + (((seg + 1) % segments) * 2)
        bottom_a = top_a + 1
        bottom_b = top_b + 1
        faces.append((top_center, top_a, top_b))
        faces.append((bottom_center, bottom_b, bottom_a))

        # Side quads as triangles.
        faces.append((top_a, bottom_a, bottom_b))
        faces.append((top_a, bottom_b, top_b))

    lines = ["# Generated by Jarvis local 3D adapter", f"# prompt: {comment}", "o jarvis_cylinder"]
    lines.extend([f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices])
    lines.extend([f"f {a} {b} {c}" for a, b, c in faces])
    return "\n".join(lines) + "\n", len(vertices), len(faces)
