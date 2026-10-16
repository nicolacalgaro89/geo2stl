"""Indexed terrain surface, closed with reversed bottom and boundary walls."""
import os
from pathlib import Path
import struct
import tempfile

import numpy as np


def surface_faces(valid):
    """Use a diagonal chosen to retain three-corner cells without bridging holes."""
    h, w = valid.shape
    faces = []
    for r in range(h - 1):
        a = np.arange(r * w, (r + 1) * w - 1)
        b, c, d = a + 1, a + w, a + w + 1
        flat = valid.ravel()
        use_bc = ~(flat[a] & flat[d]) & flat[b] & flat[c]
        for x, y, z, selection in [(a,b,d,~use_bc),(a,d,c,~use_bc),
                                    (a,b,c,use_bc),(b,d,c,use_bc)]:
            keep = selection & flat[x] & flat[y] & flat[z]
            faces.append(np.stack((x[keep],y[keep],z[keep]), axis=1))
    result = np.concatenate(faces) if faces else np.empty((0,3), dtype=np.int64)
    if not len(result):
        raise ValueError('Nessun triangolo valido: abbassare offset o stride.')
    return result


def boundary_edges(faces, check_vertices=True):
    edges = np.concatenate([faces[:,[0,1]], faces[:,[1,2]], faces[:,[2,0]]])
    canonical = np.sort(edges, axis=1)
    _, index, counts = np.unique(canonical, axis=0, return_index=True, return_counts=True)
    if np.any(counts > 2):
        raise ValueError('Superficie non manifold: un bordo appartiene a più di due facce.')
    boundary = edges[index[counts == 1]]
    _, degree = np.unique(boundary, return_counts=True)
    if check_vertices and np.any(degree != 2):
        raise ValueError('Aree valide si toccano in un solo punto: contorno non manifold. '
                         'Cambiare --stride o correggere la maschera del raster.')
    return boundary


def prepare_surface(valid, strict=False):
    """Erode point contacts so the extrusion has a manifold boundary."""
    valid = valid.copy()
    removed = 0
    for _ in range(64):
        faces = surface_faces(valid)
        edges = boundary_edges(faces, check_vertices=False)
        ids, degree = np.unique(edges, return_counts=True)
        bad = ids[degree != 2]
        if not len(bad):
            return faces, removed
        if strict:
            boundary_edges(faces)
        valid.ravel()[bad] = False
        removed += len(bad)
    raise ValueError('Contorno ancora non manifold dopo 64 passaggi; correggere la maschera.')


def write_solid(path, vertices, faces, force=False):
    boundary = boundary_edges(faces)
    count = 2 * len(faces) + 2 * len(boundary)
    if count > 0xffffffff:
        raise ValueError('Troppi triangoli per STL binario.')
    path = Path(path)
    if path.exists() and not force:
        raise ValueError(f'{path} esiste già; usare --force per sovrascrivere.')
    handle = tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False)
    temporary = Path(handle.name)
    try:
        with handle as out:
            out.write(b'geo2stl; coordinates in millimeters'.ljust(80, b'\0'))
            out.write(struct.pack('<I', count))
            dtype = np.dtype([('normal','<f4',(3,)),('vertices','<f4',(3,3)),('attribute','<u2')])
            def emit(triangles):
                triangles = np.asarray(triangles, dtype=np.float32)
                normals = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
                lengths = np.linalg.norm(normals, axis=1)
                if np.any(lengths == 0) or not np.isfinite(triangles).all() or not np.isfinite(lengths).all():
                    raise ValueError('Triangoli degeneri o coordinate fuori intervallo STL.')
                records = np.zeros(len(triangles), dtype=dtype)
                records['normal'] = normals / lengths[:,None]
                records['vertices'] = triangles
                out.write(records.tobytes())
            for start in range(0, len(faces), 10000):
                top = vertices[faces[start:start+10000]]
                emit(top)
                bottom = top[:,[2,1,0]].copy()
                bottom[:,:,2] = 0
                emit(bottom)
            for start in range(0, len(boundary), 10000):
                edge = vertices[boundary[start:start+10000]]
                a,b = edge[:,0],edge[:,1]
                a0,b0 = a.copy(),b.copy()
                a0[:,2] = b0[:,2] = 0
                emit(np.stack((b,a,a0),axis=1))
                emit(np.stack((b,a0,b0),axis=1))
        if force:
            os.replace(temporary,path)
        else:
            os.link(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)
    return count
