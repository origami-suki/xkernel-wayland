"""Explicit QEMU graphics selection; host setup alone proves no guest acceleration."""

import os
import re
import stat
from pathlib import Path


def graphics_options(backend, render_node=None):
    """Validate the requested host device and return its QEMU argument pairs."""
    if backend not in ("2d", "virgl"):
        raise ValueError(f"unsupported GPU backend: {backend}")
    if backend == "2d":
        if render_node is not None:
            raise ValueError("--render-node requires --gpu-backend virgl")
        return ["-device", "virtio-gpu-pci", "-display", "none"]
    if render_node is None:
        raise ValueError("--gpu-backend virgl requires explicit --render-node")
    try:
        resolved = Path(render_node).resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as error:
        raise ValueError(f"cannot access render node {render_node}: {error}") from error
    if not re.fullmatch(r"/dev/dri/renderD[0-9]+", str(resolved)):
        raise ValueError("render node must resolve to /dev/dri/renderD[0-9]+")
    if not stat.S_ISCHR(mode):
        raise ValueError("render node must be a DRM render character device")
    if not os.access(resolved, os.R_OK | os.W_OK):
        raise ValueError(f"render node must be readable and writable: {resolved}")
    return ["-device", "virtio-gpu-gl-pci", "-display",
            f"egl-headless,rendernode={resolved}"]


def graphics_metadata(backend, render_node, options):
    """Record selection without interpreting host initialization as guest success."""
    return {"backend": backend,
            "render_node_requested": str(render_node) if render_node is not None else None,
            "render_node_resolved": options[3].split("rendernode=", 1)[1]
            if backend == "virgl" else None,
            "host_graphics_environment": {name: os.environ.get(name) for name in
                                          ("GALLIUM_THREAD", "mesa_glthread", "DRI_PRIME",
                                           "MESA_LOADER_DRIVER_OVERRIDE", "LIBGL_ALWAYS_SOFTWARE",
                                           "EGL_PLATFORM")},
            "guest_acceleration_verified": False,
            "validation_scope": "host graphics selection only; guest DRM/virgl interfaces, "
                                "Mesa renderer and Chromium GPU acceleration remain unverified"}
