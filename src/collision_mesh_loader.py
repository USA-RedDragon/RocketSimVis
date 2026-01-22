import os
import struct
from typing import Optional, Tuple

import numpy as np

from const import ROOT_PATH


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms < 1e-6, 1.0, norms)
    return vectors / norms


def _append_plane(positions_list, normals_list, corners: np.ndarray, normal: np.ndarray) -> None:
    tri_indices = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    positions = corners[tri_indices.reshape(-1)]
    normals = np.repeat(normal.reshape(1, 3), positions.shape[0], axis=0)
    positions_list.append(positions.astype("f4"))
    normals_list.append(normals.astype("f4"))


def find_collision_mesh_root() -> Optional[str]:
    env_paths = [
        os.environ.get("ROCKETSIM_COLLISION_MESHES"),
        os.environ.get("RLGYM_COLLISION_MESHES"),
    ]
    candidate_paths = [
        *[p for p in env_paths if p],
        os.path.normpath(os.path.join(ROOT_PATH, "../collision_meshes")),
        os.path.normpath(os.path.join(ROOT_PATH, "collision_meshes")),
    ]

    for path in candidate_paths:
        if path and os.path.isdir(path):
            return path

    return None


def _read_cmf(path: str) -> Tuple[np.ndarray, np.ndarray]:
    with open(path, "rb") as handle:
        data = handle.read()

    if len(data) < 8:
        raise ValueError(f"Collision mesh file too small: {path}")

    num_tris, num_vertices = struct.unpack_from("<ii", data, 0)

    if num_tris <= 0 or num_vertices <= 0:
        raise ValueError(f"Invalid collision mesh counts in {path}: {num_tris}, {num_vertices}")

    tri_count = num_tris * 3
    vert_count = num_vertices * 3

    tri_bytes = tri_count * 4
    vert_bytes = vert_count * 4

    expected = 8 + tri_bytes + vert_bytes
    if len(data) < expected:
        raise ValueError(f"Collision mesh file truncated: {path}")

    offset = 8
    tris = np.frombuffer(data, dtype="<i4", count=tri_count, offset=offset).reshape((-1, 3))
    offset += tri_bytes
    vertices = np.frombuffer(data, dtype="<f4", count=vert_count, offset=offset).reshape((-1, 3))

    vertices = vertices * 50.0

    if np.any(tris < 0) or np.any(tris >= num_vertices):
        raise ValueError(f"Collision mesh has out-of-range vertex index: {path}")

    return vertices, tris


def load_collision_meshes_for_mode(base_dir: str, gamemode: str) -> Tuple[np.ndarray, int]:
    fallback_map = {
        "heatseeker": "soccar",
    }
    mode_dir = os.path.join(base_dir, gamemode)
    if not os.path.isdir(mode_dir):
        fallback = fallback_map.get(gamemode)
        if fallback:
            mode_dir = os.path.join(base_dir, fallback)
        if not os.path.isdir(mode_dir):
            raise FileNotFoundError(f"Missing collision mesh dir: {mode_dir}")

    mesh_files = [
        os.path.join(mode_dir, name)
        for name in sorted(os.listdir(mode_dir))
        if name.lower().endswith(".cmf")
    ]

    if not mesh_files:
        raise FileNotFoundError(f"No collision meshes found in: {mode_dir}")

    all_positions = []
    all_normals = []

    for mesh_path in mesh_files:
        vertices, tris = _read_cmf(mesh_path)

        v0 = vertices[tris[:, 0]]
        v1 = vertices[tris[:, 1]]
        v2 = vertices[tris[:, 2]]

        normals = _normalize_rows(np.cross(v1 - v0, v2 - v0))

        positions = vertices[tris.reshape(-1)]
        normals_expanded = np.repeat(normals, 3, axis=0)

        all_positions.append(positions)
        all_normals.append(normals_expanded)

    gamemode_key = gamemode.lower()
    if gamemode_key == "heatseeker":
        gamemode_key = "soccar"

    if gamemode_key == "hoops":
        extent_x = 8900.0 / 3.0
        extent_y = 3581.0
        height = 1820.0
        add_y_walls = True
    else:
        extent_x = 4096.0
        extent_y = 5120.0
        height = 2048.0
        add_y_walls = False

    floor = np.array([
        [-extent_x, -extent_y, 0.0],
        [extent_x, -extent_y, 0.0],
        [extent_x, extent_y, 0.0],
        [-extent_x, extent_y, 0.0],
    ], dtype="f4")
    ceiling = np.array([
        [-extent_x, -extent_y, height],
        [-extent_x, extent_y, height],
        [extent_x, extent_y, height],
        [extent_x, -extent_y, height],
    ], dtype="f4")

    _append_plane(all_positions, all_normals, floor, np.array([0.0, 0.0, 1.0], dtype="f4"))
    _append_plane(all_positions, all_normals, ceiling, np.array([0.0, 0.0, -1.0], dtype="f4"))

    left_wall = np.array([
        [-extent_x, -extent_y, 0.0],
        [-extent_x, extent_y, 0.0],
        [-extent_x, extent_y, height],
        [-extent_x, -extent_y, height],
    ], dtype="f4")
    right_wall = np.array([
        [extent_x, -extent_y, 0.0],
        [extent_x, -extent_y, height],
        [extent_x, extent_y, height],
        [extent_x, extent_y, 0.0],
    ], dtype="f4")
    _append_plane(all_positions, all_normals, left_wall, np.array([1.0, 0.0, 0.0], dtype="f4"))
    _append_plane(all_positions, all_normals, right_wall, np.array([-1.0, 0.0, 0.0], dtype="f4"))

    if add_y_walls:
        back_wall = np.array([
            [-extent_x, -extent_y, 0.0],
            [-extent_x, -extent_y, height],
            [extent_x, -extent_y, height],
            [extent_x, -extent_y, 0.0],
        ], dtype="f4")
        front_wall = np.array([
            [-extent_x, extent_y, 0.0],
            [extent_x, extent_y, 0.0],
            [extent_x, extent_y, height],
            [-extent_x, extent_y, height],
        ], dtype="f4")
        _append_plane(all_positions, all_normals, back_wall, np.array([0.0, 1.0, 0.0], dtype="f4"))
        _append_plane(all_positions, all_normals, front_wall, np.array([0.0, -1.0, 0.0], dtype="f4"))

    positions = np.vstack(all_positions).astype("f4")
    normals = np.vstack(all_normals).astype("f4")

    normals4 = np.concatenate([normals, np.zeros((normals.shape[0], 1), dtype="f4")], axis=1)
    packed = np.hstack([positions, normals4]).astype("f4")
    return packed, positions.shape[0]
