import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.warp import transform as project

from .mesh import prepare_surface, write_solid


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('Il valore deve essere finito e positivo.')
    return number


def finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError('Il valore deve essere finito.')
    return number


def scale(value):
    try:
        return positive(value[2:] if value.startswith('1:') else value)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise argparse.ArgumentTypeError('Scala attesa: 1:100000 oppure 100000.') from exc


def integer(value):
    try:
        n = int(value)
        if n > 0:
            return n
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Serve un intero positivo.')


def parser():
    p = argparse.ArgumentParser(description='GeoTIFF altimetrico → solido STL in millimetri.')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--info', action='store_true', help='Mostra i metadati del raster.')
    mode.add_argument('--sample', nargs=2, type=int, metavar=('X','Y'),
                      help='Ispeziona un pixel: colonna X, riga Y, indici da zero in alto a sinistra.')
    mode.add_argument('--convert', action='store_true', help='Genera un STL binario chiuso.')
    p.add_argument('file', type=Path)
    p.add_argument('-o', '--output', type=Path)
    p.add_argument('--scale', type=scale, default=100000, help='Scala orizzontale, default 1:100000.')
    p.add_argument('--elevation-offset','--elevation_offset', type=finite, default=0,
                   help='Quota di taglio in metri: conserva solo quote strettamente superiori.')
    p.add_argument('--vertical-exaggeration','--vertical_exaggeration','--vertical_exageration',
                   type=positive, default=1, help='Amplificazione verticale, default 1.')
    p.add_argument('--base-height','--base_height', type=positive, default=2, help='Spessore minimo in mm, default 2.')
    p.add_argument('--stride', type=integer, default=1, help='Campiona un pixel ogni N in entrambe le direzioni.')
    p.add_argument('--band', type=integer, default=1)
    p.add_argument('--nodata', type=finite, help='Esclude anche questo valore grezzo, oltre alla maschera del file.')
    p.add_argument('--elevation-unit', choices=['m','ft'], default=None,
                   help='Unità delle quote; usa metadati, altrimenti assume metri.')
    p.add_argument('--max-points', type=integer, default=2000000, help='Limite griglia campionata (default 2000000).')
    p.add_argument('--json', action='store_true', help='Output JSON con --info o --sample.')
    p.add_argument('--stats', action='store_true', help='Calcola statistiche esatte con --info, leggendo il raster.')
    p.add_argument('--strict-boundary', action='store_true', help='Rifiuta contatti puntuali invece di rimuoverli dal contorno.')
    p.add_argument('--force', action='store_true', help='Consente di sovrascrivere lo STL.')
    return p


def unit_factor(src, args):
    unit = args.elevation_unit or src.units[args.band - 1] or 'm'
    if unit.lower() in ('m','meter','meters','metre','metres'):
        return 1
    if unit.lower() in ('ft','foot','feet'):
        return 0.3048
    raise ValueError(f'Unità verticale non supportata: {unit}; specificare --elevation-unit.')


def valid_values(src, args, window=None):
    raw = src.read(args.band, window=window, masked=True)
    values = np.asarray(raw.data, dtype=np.float64)
    valid = ~np.ma.getmaskarray(raw) & np.isfinite(values)
    if args.nodata is not None:
        valid &= values != args.nodata
    values = (values * src.scales[args.band - 1] + src.offsets[args.band - 1]) * unit_factor(src,args)
    valid &= np.isfinite(values)
    return values, valid


def sample(src, args):
    x, y = args.sample
    if not (0 <= x < src.width and 0 <= y < src.height):
        raise ValueError(f'Pixel ({x}, {y}) fuori dal raster: X da 0 a {src.width-1}, '
                         f'Y da 0 a {src.height-1}.')
    window = Window(x,y,1,1)
    raw = src.read(args.band,window=window)[0,0].item()
    mask = int(src.read_masks(args.band,window=window)[0,0])
    values, valid = valid_values(src,args,window)
    elevation = float(values[0,0])
    coordinate_x, coordinate_y = src.xy(y,x)
    def serializable(value):
        return value if value is None or math.isfinite(value) else str(value)
    result = dict(file=str(args.file), band=args.band, pixel_x=x, pixel_y=y,
                  raw_value=serializable(raw), elevation_m=serializable(elevation),
                  elevation_unit=args.elevation_unit or src.units[args.band-1] or 'm',
                  elevation_unit_assumed=not bool(args.elevation_unit or src.units[args.band-1]),
                  nodata=serializable(src.nodatavals[args.band-1]), mask_value=mask,
                  valid_in_file=bool(mask > 0), valid_with_options=bool(valid[0,0]),
                  above_elevation_offset=bool(math.isfinite(elevation) and elevation > args.elevation_offset),
                  eligible_for_mesh=bool(valid[0,0] and elevation > args.elevation_offset),
                  coordinate_x=coordinate_x, coordinate_y=coordinate_y,
                  crs=str(src.crs) if src.crs else None)
    if args.json:
        print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    else:
        for key,value in result.items():
            print(f'{key}: {value}')


def information(src, args):
    result = dict(file=str(args.file), driver=src.driver, width=src.width,height=src.height,
                  bands=src.count, dtypes=src.dtypes, crs=str(src.crs) if src.crs else None,
                  bounds=list(src.bounds), pixel_size=list(src.res), transform=list(src.transform)[:6],
                  nodata=src.nodata if src.nodata is None or math.isfinite(src.nodata) else str(src.nodata),
                  mask_flags=[[f.name for f in flags] for flags in src.mask_flag_enums],
                  scales=src.scales, offsets=src.offsets, elevation_units=src.units,
                  elevation_unit_assumed='m' if not src.units[args.band-1] and not args.elevation_unit else None,
                  sampled_grid=[math.ceil(src.height/args.stride), math.ceil(src.width/args.stride)],
                  tags=src.tags(), band_tags=src.tags(args.band))
    if args.stats:
        count, zeros, minimum, maximum = 0,0,math.inf,-math.inf
        for _,window in src.block_windows(args.band):
            values, valid = valid_values(src,args,window)
            count += int(valid.sum())
            zeros += int(np.sum(valid & (values == 0)))
            if valid.any():
                minimum = min(minimum,float(values[valid].min()))
                maximum = max(maximum,float(values[valid].max()))
        result['statistics'] = dict(valid_pixels=count,invalid_pixels=src.width*src.height-count,
                                    zero_pixels=zeros,min_m=minimum if count else None,max_m=maximum if count else None)
    if args.json:
        print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    else:
        for key,value in result.items():
            print(f'{key}: {value}')
        if src.nodata is None:
            print('Nota: NoData non dichiarato. Per escludere gli zeri usare --nodata 0.')


def convert(src, args):
    if not src.crs:
        raise ValueError('CRS assente: assegnare al raster il sistema di coordinate corretto.')
    height,width = math.ceil(src.height/args.stride),math.ceil(src.width/args.stride)
    if height < 2 or width < 2:
        raise ValueError('Il campionamento richiede almeno 2 × 2 punti.')
    if height*width > args.max_points:
        suggested = max(args.stride+1,math.ceil(math.sqrt(src.width*src.height/args.max_points)))
        raise ValueError(f'Griglia di {height*width:,} punti oltre --max-points. Provare --stride {suggested}.')
    output = args.output or args.file.with_suffix('.stl')
    if output.resolve() == args.file.resolve():
        raise ValueError('Il file di output deve essere diverso dal GeoTIFF di input.')
    if output.exists() and not args.force:
        raise ValueError(f'{output} esiste già; usare --force.')
    values = np.empty((height,width),dtype=np.float64)
    valid = np.empty((height,width),dtype=bool)
    # Read strips: preserve actual source pixel centers, including rotated transforms.
    batch = max(1,256//args.stride)
    for i in range(0,height,batch):
        row = i*args.stride
        size = min(batch*args.stride,src.height-row)
        z,mask = valid_values(src,args,Window(0,row,src.width,size))
        sampled = z[::args.stride,::args.stride]
        values[i:i+len(sampled)] = sampled
        valid[i:i+len(sampled)] = mask[::args.stride,::args.stride]
    valid &= values > args.elevation_offset
    rows,cols = np.indices(values.shape,dtype=np.float64)
    cols = cols.ravel()*args.stride+0.5
    rows = rows.ravel()*args.stride+0.5
    t = src.transform
    x,y = t.a*cols+t.b*rows+t.c,t.d*cols+t.e*rows+t.f
    if src.crs.is_geographic:
        center_x,center_y = t*(src.width/2,src.height/2)
        lon,lat = project(src.crs,'EPSG:4326',[center_x],[center_y])
        local = f'+proj=aeqd +lat_0={lat[0]} +lon_0={lon[0]} +datum=WGS84 +units=m +no_defs'
        x,y = project(src.crs,local,x.tolist(),y.tolist())
        x,y = np.asarray(x),np.asarray(y)
        print('Coordinate geografiche convertite in proiezione metrica locale AEQD.',file=sys.stderr)
    elif src.crs.is_projected:
        factor = src.crs.linear_units_factor[1]
        x,y = x*factor,y*factor
    else:
        raise ValueError('Serve un CRS geografico o proiettato.')
    vertices = np.column_stack((x,y,values.ravel()))
    faces, removed = prepare_surface(valid,args.strict_boundary)
    if removed:
        print(f'Contorno regolarizzato: esclusi {removed} punti che creavano contatti non manifold.',file=sys.stderr)
    # Raster row direction can reverse orientation. Determine it in projected XY.
    tri = vertices[faces[0]]
    signed_area = (tri[1,0]-tri[0,0])*(tri[2,1]-tri[0,1])-(tri[1,1]-tri[0,1])*(tri[2,0]-tri[0,0])
    if signed_area < 0:
        faces = faces[:,[0,2,1]]
    used = np.unique(faces)
    vertices[:,:2] -= vertices[used,:2].min(axis=0)
    vertices[:,:2] *= 1000/args.scale
    vertices[:,2] = args.base_height+(vertices[:,2]-args.elevation_offset)*1000/args.scale*args.vertical_exaggeration
    extent = vertices[used].max(axis=0)
    count = write_solid(output,vertices,faces,args.force)
    print(f'STL: {output}\nTriangoli: {count:,}\nDimensioni XYZ: {extent[0]:.3f} × {extent[1]:.3f} × {extent[2]:.3f} mm')
    if not src.units[args.band-1] and args.elevation_unit is None:
        print('Unità verticale assunta: metri (metadato assente).',file=sys.stderr)


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        with rasterio.open(args.file) as src:
            if args.band > src.count:
                raise ValueError(f'Banda {args.band} assente; il raster ha {src.count} bande.')
            if args.sample is not None:
                sample(src,args)
            elif args.info:
                information(src,args)
            else:
                convert(src,args)
        return 0
    except (ValueError,OSError,rasterio.errors.RasterioError) as exc:
        print(f'Errore: {exc}',file=sys.stderr)
        return 1
