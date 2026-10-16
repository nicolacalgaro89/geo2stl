from collections import Counter
import struct

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin, Affine

from geo2stl.cli import main
from geo2stl.mesh import prepare_surface, write_solid


def read_stl(path):
    data = path.read_bytes()
    count, = struct.unpack_from('<I', data,80)
    assert len(data) == 84+count*50
    dtype=np.dtype([('n','<f4',(3,)),('v','<f4',(3,3)),('a','<u2')])
    records=np.frombuffer(data, dtype=dtype,offset=84)
    return records['v'].astype(float),records['n']


def assert_solid(path):
    triangles,normals=read_stl(path)
    edges=Counter()
    directions=Counter()
    for triangle in triangles:
        for a,b in zip(triangle, np.roll(triangle,-1,axis=0)):
            a,b=tuple(a),tuple(b)
            edges[tuple(sorted((a,b)))] += 1
            directions[(a,b)] += 1
    assert set(edges.values()) == {2}
    assert all(directions[(b,a)] == n for (a,b),n in directions.items())
    cross=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    assert np.all(np.einsum('ij,ij->i',cross,normals)>0)
    volume=np.einsum('ij,ij->i',triangles[:,0],np.cross(triangles[:,1],triangles[:,2])).sum()/6
    assert volume > 0
    return triangles,volume


def raster(tmp_path, values, nodata=None, transform=None, crs='EPSG:32632', **kw):
    path=tmp_path/'dem.tif'
    with rasterio.open(path,'w',driver='GTiff',width=values.shape[1],height=values.shape[0],
                       count=1,dtype='float32',crs=crs,transform=transform or from_origin(500000,5100000,30,30),nodata=nodata) as dst:
        dst.write(values.astype('float32'),1)
        if 'mask' in kw:
            dst.write_mask(kw['mask'].astype('uint8')*255)
    return path


def test_flat_model_dimensions_and_volume(tmp_path):
    src=raster(tmp_path,np.full((3,4),300))
    out=tmp_path/'model.stl'
    assert main(['--convert',str(src),'-o',str(out),'--scale','1:100000','--elevation_offset','200',
                 '--vertical_exageration','1.5','--base_height','2']) == 0
    triangles,volume=assert_solid(out)
    np.testing.assert_allclose(triangles.reshape(-1,3).max(axis=0),[.9,.6,3.5],rtol=1e-6)
    assert volume == pytest.approx(.9*.6*3.5,rel=1e-6)


@pytest.mark.parametrize('kind',['nodata','explicit','mask','nan','threshold'])
def test_hole_has_no_bottom(tmp_path,kind):
    values=np.full((5,5),300.)
    values[2,2]= {'nodata':-9999,'explicit':0,'mask':300,'nan':np.nan,'threshold':200}[kind]
    mask=np.ones((5,5),bool);mask[2,2]=False
    src=raster(tmp_path,values,nodata=-9999 if kind=='nodata' else None,
               **({'mask':mask} if kind=='mask' else {}))
    out=tmp_path/'hole.stl'
    argv=['--convert',str(src),'-o',str(out),'--elevation-offset','200']
    if kind=='explicit':
        argv.extend(['--nodata','0'])
    assert main(argv)==0
    triangles,_=assert_solid(out)
    bottom=triangles[np.all(triangles[:,:,2]==0,axis=1)]
    # Center of the hole lies inside no bottom triangle (strict barycentric inclusion).
    for tri in bottom[:,:,:2]:
        a,b,c=tri
        u,v=np.linalg.solve(np.column_stack((b-a,c-a)),np.array([.6,.6])-a)
        assert not (u>=-1e-6 and v>=-1e-6 and u+v<=1+1e-6)


def test_point_contacts_cleaned_and_strict_rejected(tmp_path):
    mask=np.array([[1,1,1,0,0],[1,1,0,0,1],[1,0,1,1,1],
                   [1,1,1,1,0],[1,0,1,0,1]],bool)
    with pytest.raises(ValueError,match='non manifold'):
        prepare_surface(mask,strict=True)
    faces,removed=prepare_surface(mask)
    assert removed>0
    rows,cols=np.indices(mask.shape)
    vertices=np.column_stack((cols.ravel(),rows.ravel(),np.full(mask.size,2.)))
    out=tmp_path/'clean.stl'
    write_solid(out,vertices,faces)
    assert_solid(out)


def test_zero_is_valid_without_override(tmp_path):
    src=raster(tmp_path,np.zeros((2,2)))
    out=tmp_path/'zero.stl'
    assert main(['--convert',str(src),'-o',str(out),'--elevation-offset','-1'])==0
    assert_solid(out)
    assert main(['--convert',str(src),'-o',str(out),'--force','--elevation-offset','-1','--nodata','0'])==1
    assert_solid(out)  # Failed conversion keeps the earlier output.


@pytest.mark.parametrize('transform,crs',[(from_origin(11,46,.001,.001),'EPSG:4326'),
                                        (Affine(20,5,500000,3,30,5100000),'EPSG:32632')])
def test_geographic_and_rotated_projection(tmp_path,transform,crs):
    src=raster(tmp_path,np.full((4,4),100.),transform=transform,crs=crs)
    out=tmp_path/'projection.stl'
    assert main(['--convert',str(src),'-o',str(out)])==0
    assert_solid(out)


def test_cli_limits_metadata_and_no_overwrite(tmp_path,capsys):
    src=raster(tmp_path,np.full((4,4),100.))
    assert main(['--info',str(src),'--stats','--json'])==0
    import json
    metadata=json.loads(capsys.readouterr().out)
    assert metadata['statistics']['valid_pixels']==16
    assert main(['--convert',str(src),'--max-points','4'])==1
    out=tmp_path/'existing.stl';out.write_bytes(b'keep')
    assert main(['--convert',str(src),'-o',str(out)])==1
    assert out.read_bytes()==b'keep'
    assert main(['--convert',str(src),'-o',str(src),'--force'])==1
    assert main(['--convert',str(src),'--band','2'])==1


def test_vertical_metadata_scale_and_feet(tmp_path):
    src=raster(tmp_path,np.full((2,2),100.))
    with rasterio.open(src,'r+') as dst:
        dst.scales=[2.]
        dst.offsets=[10.]
        dst.set_band_unit(1,'ft')
    out=tmp_path/'feet.stl'
    assert main(['--convert',str(src),'-o',str(out),'--scale','1000'])==0
    triangles,_=assert_solid(out)
    assert triangles[:,:,2].max()==pytest.approx(2+210*.3048,rel=1e-6)


def test_sample_xy_raw_scaled_mask_and_threshold(tmp_path,capsys):
    import json
    src=raster(tmp_path,np.array([[0,10,20],[30,40,50]]),nodata=0)
    with rasterio.open(src,'r+') as dst:
        dst.scales=[2.]
        dst.offsets=[5.]
    assert main(['--sample','2','1',str(src),'--json','--elevation-offset','105'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['raw_value']==50
    assert result['elevation_m']==105
    assert result['valid_in_file'] is True
    assert result['eligible_for_mesh'] is False
    assert result['coordinate_x']==500075
    assert result['coordinate_y']==5099955
    assert main(['--sample','0','0',str(src),'--json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['mask_value']==0
    assert result['valid_with_options'] is False
    assert main(['--sample','1','0',str(src),'--nodata','10','--json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['valid_in_file'] is True
    assert result['valid_with_options'] is False


@pytest.mark.parametrize('x,y',[(-1,0),(0,-1),(3,0),(0,2)])
def test_sample_rejects_outside_raster(tmp_path,x,y):
    src=raster(tmp_path,np.ones((2,3)))
    assert main(['--sample',str(x),str(y),str(src)])==1


def test_sample_nan_json(tmp_path,capsys):
    import json
    src=raster(tmp_path,np.array([[np.nan,1],[2,3]]))
    assert main(['--sample','0','0',str(src),'--json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['raw_value']=='nan'
    assert result['valid_with_options'] is False
