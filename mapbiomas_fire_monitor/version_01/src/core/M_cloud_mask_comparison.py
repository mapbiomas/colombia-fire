"""Compare native SCL and the CloudSEN12 L2A model on downloaded scenes.

Model/reference: https://github.com/IPL-UV/cloudsen12_models
Inference uses calibrated L2A reflectance on the native 10 m NIR grid, in blocks.
"""
from contextlib import ExitStack, nullcontext
import hashlib
import json
import textwrap
from pathlib import Path

import numpy as np

MODEL_NAME = 'cloudsen12l2a'
MODEL_REVISION = 'cloudsen12_models==1.0.2/cloudsen12l2a'
L2A_ASSETS = {
    'B01': 'coastal', 'B02': 'blue', 'B03': 'green', 'B04': 'red',
    'B05': 'rededge1', 'B06': 'rededge2', 'B07': 'rededge3', 'B08': 'nir',
    'B8A': 'nir08', 'B09': 'nir09', 'B11': 'swir16', 'B12': 'swir22',
}
NODATA = 255


def scl_cloud_classes(scl):
    """Shared comparison codes: 0 clear, 1 thick cloud, 2 cirrus, 3 shadow, 255 invalid."""
    classes = np.zeros(scl.shape, dtype='uint8')
    classes[np.isin(scl, (0, 1))] = NODATA
    classes[np.isin(scl, (8, 9))] = 1
    classes[scl == 10] = 2
    classes[scl == 3] = 3
    return classes


def _calibration(source, metadata):
    scale = metadata.get('scale', source.scales[0])
    offset = metadata.get('offset', source.offsets[0])
    if np.issubdtype(np.dtype(source.dtypes[0]), np.integer) and scale == 1 and 'scale' not in metadata:
        raise ValueError(f'Falta calibración de reflectancia en {source.name}; vuelva a ejecutar la descarga.')
    if not np.isfinite(scale) or scale <= 0 or not np.isfinite(offset):
        raise ValueError(f'Calibración inválida en {source.name}.')
    return float(scale), float(offset)


def _load_model(weights_dir, *, use_gpu=True):
    import torch
    from cloudsen12_models import cloudsen12
    device = torch.device('cuda' if use_gpu and torch.cuda.is_available() else 'cpu')
    print(f'CloudSEN12 L2A · dispositivo: {device}')
    return cloudsen12.load_model_by_name(MODEL_NAME, weights_folder=str(weights_dir), device=device)


def _model_assets(model):
    bands = [f'B0{b[1:]}' if len(b) == 2 else b for b in model.bands]
    if len(bands) != 12 or set(bands) != set(L2A_ASSETS):
        raise ValueError('Se requiere el modelo cloudsen12l2a de 12 bandas; no un modelo L1C.')
    return [L2A_ASSETS[band] for band in bands]


def _region_geometries(region_path, crs):
    import rasterio.warp
    if region_path is None:
        return None
    features = json.loads(Path(region_path).read_text())['features']
    return [rasterio.warp.transform_geom('EPSG:4326', crs, f['geometry']) for f in features]


def process_scene(scene_id, paths, metadata, output_dir, model, *, region_path=None,
                  block_size=512, halo=64, overwrite=False, monitor=None):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import geometry_mask, geometry_window
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window

    ordered = _model_assets(model)
    def phase(name, *, gpu=False):
        return monitor.phase(name, scene_id, gpu=gpu) if monitor is not None else nullcontext()
    if block_size < 32 or block_size % 32 or halo < 0:
        raise ValueError('Use bloques múltiplos de 32 y un margen no negativo.')
    folder = Path(output_dir) / scene_id
    folder.mkdir(parents=True, exist_ok=True)
    scl_path, model_path = folder / 'scl_cloud_classes.tif', folder / 'cloudsen12_classes.tif'
    info_path = folder / 'cloud_masks.json'
    signature_data = {'model': MODEL_REVISION, 'block_size': block_size, 'halo': halo,
                      'inputs': {key: [str(Path(path).resolve()), Path(path).stat().st_size,
                                       Path(path).stat().st_mtime_ns, metadata.get(key, {})]
                                 for key, path in sorted(paths.items())},
                      'region': Path(region_path).read_text() if region_path else None}
    signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True).encode()).hexdigest()
    if not overwrite and info_path.is_file() and scl_path.is_file() and model_path.is_file():
        saved = json.loads(info_path.read_text())
        if saved.get('signature') == signature:
            return dict(saved, reused=True)

    info_path.unlink(missing_ok=True)
    partial_scl = scl_path.with_suffix('.partial.tif')
    partial_model = model_path.with_suffix('.partial.tif')
    try:
        with ExitStack() as stack:
            with phase('Preparación compartida'):
                reference = stack.enter_context(rasterio.open(paths['nir']))
                if reference.crs is None or not reference.crs.is_projected or not np.allclose(reference.res, (10, 10)):
                    raise ValueError('El modelo requiere la malla NIR nativa de 10 m con CRS proyectado.')
                geometries = _region_geometries(region_path, reference.crs)
                crop = (geometry_window(reference, geometries) if geometries else
                        Window(0, 0, reference.width, reference.height))
                width, height = int(crop.width), int(crop.height)
                transform = reference.window_transform(crop)
                readers, calibrations = {}, {}
                for asset in (*ordered, 'scl'):
                    source = stack.enter_context(rasterio.open(paths[asset]))
                    if asset != 'scl':
                        calibrations[asset] = _calibration(source, metadata.get(asset, {}))
                    readers[asset] = stack.enter_context(WarpedVRT(
                        source, crs=reference.crs, transform=transform, width=width, height=height,
                        src_nodata=source.nodata if source.nodata is not None else 0,
                        nodata=0 if asset == 'scl' else float('nan'), dtype='float32',
                        resampling=Resampling.nearest if asset == 'scl' else Resampling.bilinear))
                profile = dict(driver='GTiff', width=width, height=height, count=1, dtype='uint8',
                               crs=reference.crs, transform=transform, nodata=NODATA,
                               tiled=True, blockxsize=256, blockysize=256,
                               compress='DEFLATE', BIGTIFF='IF_SAFER')
                scl_output = stack.enter_context(rasterio.open(partial_scl, 'w', **profile))
                model_output = stack.enter_context(rasterio.open(partial_model, 'w', **profile))
                for out in (scl_output, model_output):
                    out.update_tags(classes='0=clear,1=thick_cloud,2=thin_cloud_or_cirrus,3=cloud_shadow,255=nodata')
            for row in range(0, height, block_size):
                for col in range(0, width, block_size):
                    with phase('Preparación compartida'):
                        core = Window(col, row, min(block_size, width-col), min(block_size, height-row))
                        left, top = max(0, col-halo), max(0, row-halo)
                        right, bottom = min(width, col+core.width+halo), min(height, row+core.height+halo)
                        expanded = Window(left, top, right-left, bottom-top)
                        shape = (int(expanded.height), int(expanded.width))
                        cube = np.empty((12, *shape), dtype='float32')
                        valid = np.ones(shape, dtype=bool)
                        for i, asset in enumerate(ordered):
                            values = readers[asset].read(1, window=expanded, masked=True).filled(np.nan)
                            scale, offset = calibrations[asset]
                            cube[i] = values * scale + offset
                            valid &= np.isfinite(cube[i])
                        scl = readers['scl'].read(1, window=expanded).astype('uint8')
                        valid &= ~np.isin(scl, (0, 1))
                        if geometries:
                            valid &= geometry_mask(geometries, out_shape=shape,
                                                   transform=readers['nir'].window_transform(expanded), invert=True)
                        core_slice = (slice(row-top, row-top+int(core.height)),
                                      slice(col-left, col-left+int(core.width)))
                    with phase('SCL'):
                        native = scl_cloud_classes(scl)
                        native[~valid] = NODATA
                        scl_output.write(native[core_slice], 1, window=core)
                    with phase('CloudSEN12', gpu=monitor is not None and monitor.cuda is not None):
                        predicted = np.full(shape, NODATA, dtype='uint8')
                        if valid.any():
                            prediction = np.asarray(model.predict(np.nan_to_num(cube, nan=0, posinf=0, neginf=0)))
                            if prediction.shape != shape or not np.isin(prediction, (0, 1, 2, 3)).all():
                                raise ValueError('CloudSEN12 debe devolver una máscara H×W con clases 0–3.')
                            predicted[valid] = prediction[valid].astype('uint8')
                        model_output.write(predicted[core_slice], 1, window=core)
                    del cube, values, valid, predicted, native, scl
                    if 'prediction' in locals():
                        del prediction
        partial_scl.replace(scl_path)
        partial_model.replace(model_path)
        record = {'scene_id': scene_id, 'signature': signature, 'model': MODEL_REVISION,
                  'scl_path': str(scl_path), 'model_path': str(model_path),
                  'spectral_paths': {key: str(value) for key, value in paths.items() if key != 'scl'},
                  'calibration': {key: list(value) for key, value in calibrations.items()},
                  'resolution_m': 10, 'shape': [height, width], 'reused': False}
        info_path.write_text(json.dumps(record, indent=2))
        return record
    finally:
        partial_scl.unlink(missing_ok=True)
        partial_model.unlink(missing_ok=True)


def _preview_scene(record, max_pixels=512):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    with rasterio.open(record['model_path']) as grid:
        factor = min(1, max_pixels / max(grid.width, grid.height))
        shape = (max(1, round(grid.height*factor)), max(1, round(grid.width*factor)))
        classes = grid.read(1, out_shape=shape, resampling=Resampling.nearest)
        rgb = []
        for asset in ('swir16', 'nir', 'red'):
            with rasterio.open(record['spectral_paths'][asset]) as source, WarpedVRT(
                source, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                src_nodata=source.nodata if source.nodata is not None else 0,
                nodata=float('nan'), dtype='float32', resampling=Resampling.bilinear) as vrt:
                values = vrt.read(1, out_shape=shape, masked=True, resampling=Resampling.average).filled(np.nan)
                scale, offset = record['calibration'][asset]
                rgb.append(np.clip((values * scale + offset - .03) / (.4 - .03), 0, 1))
        rgb = np.moveaxis(np.stack(rgb), 0, -1)
        rgb[classes == NODATA] = np.nan
    with rasterio.open(record['scl_path']) as scl:
        native = scl.read(1, out_shape=shape, resampling=Resampling.nearest)
    return rgb, native, classes


def plot_comparison(records, output_path):
    """Always 3 rows × 10 columns; fewer scenes leave explicitly empty columns."""
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(3, 10, figsize=(30, 9), squeeze=False, layout='constrained')
    labels = ('Original', 'SCL aplicada', 'CloudSEN12 L2A aplicada')
    for column in range(10):
        for row in range(3):
            axes[row, column].set_xticks([])
            axes[row, column].set_yticks([])
            axes[row, column].set_facecolor('#202020')
            if column == 0:
                axes[row, column].set_ylabel(labels[row], fontsize=9)
        if column >= len(records):
            axes[0, column].set_title('Sin escena')
            continue
        rgb, scl, model = _preview_scene(records[column])
        axes[0, column].imshow(rgb)
        axes[1, column].imshow(np.where((scl == 0)[..., None], rgb, np.nan))
        axes[2, column].imshow(np.where((model == 0)[..., None], rgb, np.nan))
        title = textwrap.fill(f'{column+1}. {records[column]["scene_id"]}', width=25)
        axes[0, column].set_title(title, fontsize=6)
    figure.suptitle('SWIR1/NIR/rojo · píxeles excluidos en negro · primeras 10 escenas', fontsize=13)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=150)
    plt.show()
    return figure


def process_downloaded_scenes(monthly_download, output_dir, *, region_path=None, debug=False,
                               weights_dir=None, model=None, block_size=512, halo=64, overwrite=False,
                               use_gpu=True, live_monitor=False):
    result = monthly_download.get('result')
    if result is None or not result.paths:
        raise ValueError('No hay escenas descargadas; ejecute primero la celda de descarga mensual.')
    required = {*L2A_ASSETS.values(), 'scl'}
    for scene_id, paths in result.paths.items():
        missing = required - paths.keys()
        if missing:
            raise ValueError(f'{scene_id}: faltan {sorted(missing)}. Vuelva a ejecutar la descarga para completar las 12 bandas L2A y SCL.')
        for path in paths.values():
            if not Path(path).is_file():
                raise FileNotFoundError(path)
    from M_resource_monitor import ResourceMonitor
    output_dir = Path(output_dir)
    records = []
    output = {'scenes': records, 'figure_path': None}
    with ResourceMonitor(output_dir, live=live_monitor) as monitor:
        if model is None:
            import torch
            device = torch.device('cuda' if use_gpu and torch.cuda.is_available() else 'cpu')
            if device.type == 'cuda':
                monitor.configure_gpu(torch.cuda, device)
            with monitor.phase('Carga del modelo', gpu=device.type == 'cuda'):
                model = _load_model(weights_dir or output_dir / 'weights', use_gpu=use_gpu)
        else:
            device = str(getattr(model, 'device', 'cpu'))
            if device.startswith('cuda'):
                if not use_gpu:
                    raise ValueError('El modelo proporcionado está en CUDA, pero USAR_GPU está desactivado.')
                import torch
                monitor.configure_gpu(torch.cuda, getattr(model, 'device'))
        _model_assets(model)
        metadata = {(row['item_id'], row['asset']): (row.get('raster_bands') or [{}])[0]
                    for row in result.manifest}
        for scene_id, paths in result.paths.items():
            print(f'Procesando nubes y sombras: {scene_id} ({len(records)+1}/{len(result.paths)})')
            records.append(process_scene(scene_id, paths,
                                         {asset: metadata.get((scene_id, asset), {}) for asset in paths},
                                         output_dir, model, region_path=region_path, block_size=block_size,
                                         halo=halo, overwrite=overwrite, monitor=monitor))
        reused = sum(record.get('reused', False) for record in records)
        if reused:
            note = (f'{reused} escenas reutilizadas desde caché: sin nuevas mediciones SCL/CloudSEN12. '
                    'Active SOBRESCRIBIR_MASCARAS para medirlas nuevamente.')
            monitor.notes.append(note)
            print(note)
        if debug:
            output['figure_path'] = str(output_dir / 'cloud_mask_comparison_3x10.png')
            with monitor.phase('Visualización debug'):
                plot_comparison(records, output['figure_path'])
    output.update(resources=monitor.summary(), resources_path=str(output_dir / 'resource_usage.json'),
                  comparison_path=str(output_dir / 'resource_comparison.csv'),
                  processed_scenes=len(records)-reused, cached_scenes=reused)
    print(f'Máscaras guardadas para {len(records)} escenas en {output_dir}.')
    return output
