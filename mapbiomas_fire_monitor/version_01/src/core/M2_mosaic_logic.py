"""
M2 - Processamento e Download (Lógica)
Converte shards (GCS) em COGs nacionais através do GDAL (Sequencial/Monoprocess)
"""
import os
import subprocess
import glob
import time
import re

from M0_auth_config import (
    CONFIG, mosaic_name, _get_fs,
    monthly_chunk_path, monthly_mosaic_path, monthly_cog_path,
    yearly_chunk_path, yearly_mosaic_path, yearly_cog_path,
    get_temp_dir, check_command_exists
)
from M_gcs import upload, download

# Mapeamento de Tipos de Dados Recomendado (IPAM/MapBiomas)
BAND_DATATYPES = {
    'blue': 'Int16', 'green': 'Int16', 'red': 'Int16', 
    'nir': 'Int16', 'swir1': 'Int16', 'swir2': 'Int16',
    'nbr': 'Int16', 'dayOfYear': 'Int16',
    'classification': 'Byte', 'burned_area': 'Byte',
    'probability': 'Float32', 'score': 'Float32'
}

def list_gcs_files(prefix, logger=None):
    """Lista shards no GCS usando gcsfs."""
    fs = _get_fs()
    clean_prefix = prefix.replace(f"gs://{CONFIG['bucket']}/", "").strip("/") + "/"
    
    if logger: logger(f"Searching shards in: gs://{CONFIG['bucket']}/{clean_prefix}", "info")
    
    try:
        files = fs.find(f"gs://{CONFIG['bucket']}/{clean_prefix}")
        tif_files = [f for f in files if f.endswith('.tif')]
        return [f if f.startswith('gs://') else f"gs://{f}" for f in tif_files]
    except Exception as e:
        if logger: logger(f"Error accessing GCS: {e}", "error")
        return []

def check_m2_dependencies():
    """Valida se utilitarios estao integrados."""
    from M0_auth_config import ensure_gdal_path
    ensure_gdal_path()
    
    deps = {
        'gsutil':  'gsutil' if os.name != 'nt' else 'gsutil.cmd',
        'gdalbuildvrt': 'gdalbuildvrt',
        'gdal_translate': 'gdal_translate'
    }
    return [name for name, cmd in deps.items() if not check_command_exists(cmd)]

def run_cmd(args, label="Comando"):
    """Envolve comando subprocess com traces mais seguros"""
    try:
        res = subprocess.run(args, capture_output=True, text=True, check=True)
        return res
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Error in {label}: {e.stderr.strip() if e.stderr else e.stdout.strip()}")

def assemble_country_mosaic(year, month=None, period='monthly', bands=None, sensor=None, mosaic_method='minnbr', logger=None, progress_idx=0, progress_total=0, start_time=None, strict_names=False):
    import shutil
    from M0_auth_config import GLOBAL_OPTS, _single

    s = sensor or _single(GLOBAL_OPTS['SENSOR'])
    m_method = mosaic_method or 'minnbr'

    if period == 'monthly':
        chunk_prefix  = monthly_chunk_path(year, month, mosaic=m_method, sensor=s)
        mosaic_prefix = monthly_cog_path(year, month, mosaic=m_method, sensor=s)
        base_name = mosaic_name(year, month, 'monthly', mosaic=m_method, sensor=s)
        label = f"{year}-{month:02d}"
    else:
        chunk_prefix  = yearly_chunk_path(year, mosaic=m_method, sensor=s)
        mosaic_prefix = yearly_cog_path(year, mosaic=m_method, sensor=s)
        base_name = mosaic_name(year, period='yearly', mosaic=m_method, sensor=s)
        label = f"{year} Annual"

    missing = check_m2_dependencies()
    # Listing uses gcsfs; the shared download/upload helpers still use gsutil.
    if missing:
        msg = f" Missing vital dependencies: {missing}"
        if logger: logger(msg, "error")
        raise RuntimeError(msg)

    work_root = get_temp_dir()
    session_id = f"m2_{base_name}_{int(time.time())}"
    tmp_path = os.path.join(work_root, session_id)
    os.makedirs(tmp_path, exist_ok=True)

    results = []

    try:
        # Busca direta no GCS
        gcs_files = list_gcs_files(chunk_prefix, logger=logger)
        
        if not gcs_files:
            if logger: logger(f"No shard found in GCS for {label}.", "warning")
            return []
            
        found_bands = {}
        for f in gcs_files:
            fname = os.path.basename(f)
            # Match mais flexível: busca a banda cercada por separadores
            for b_name in (bands or CONFIG['bands_all']):
                if strict_names:
                    expected = mosaic_name(year, month, period, band=b_name, sensor=s, mosaic=m_method)
                    if _matches_chunk_name(fname, expected):
                        found_bands.setdefault(b_name, []).append(f)
                        break
                    continue
                # Procuramos a banda como um "token" isolado no nome do arquivo
                # Ex: _blue_ ou _blue.tif ou _blue-
                if f"_{b_name}_" in fname or f"_{b_name}." in fname:
                    if b_name not in found_bands: found_bands[b_name] = []
                    found_bands[b_name].append(f)
                    break
        
        # Log para conferência
        if logger:
            for b, f_list in found_bands.items():
                logger(f"[DEBUG] Band {b}: {len(f_list)} shards found.", "info")
        
        target_bands = bands or CONFIG['bands_all']
        bands_to_process = {b: found_bands[b] for b in target_bands if b in found_bands}
        
        if not bands_to_process:
            if logger: logger(f"Target bands {target_bands} not detected in GCS for {base_name}.", "warning")
            return []

        current_step = progress_idx

        for b_name, remote_shards in bands_to_process.items():
            clean_b_name = "dayOfYear" if b_name.lower() == "dayofyear" else b_name
            current_step += 1
            
            if logger: logger(f"Processing [{b_name}] ({len(remote_shards)} shards)...", "info")
            
            band_tmp = os.path.join(tmp_path, b_name)
            os.makedirs(band_tmp, exist_ok=True)

            try:
                for shard_url in remote_shards:
                    local_shard = os.path.join(band_tmp, os.path.basename(shard_url))
                    download(shard_url, local_shard)

                local_shards = glob.glob(os.path.join(band_tmp, '*.tif'))
                if not local_shards: continue

                vrt_path = os.path.join(tmp_path, f"{base_name}_{b_name}.vrt")
                run_cmd(['gdalbuildvrt', vrt_path] + local_shards, label=f"VRT ({b_name})")

                cog_remote_name = f"{mosaic_name(year, month, period, clean_b_name, sensor=s, mosaic=m_method)}_cog.tif"
                cog_local_path = os.path.join(tmp_path, cog_remote_name)
                
                dt = BAND_DATATYPES.get(clean_b_name, 'Int16')
                
                run_cmd([
                    'gdal_translate', '-of', 'COG', '-ot', dt,
                    '-co', 'COMPRESS=LZW', '-co', 'PREDICTOR=2',
                    '-co', 'NUM_THREADS=2', '-co', 'BIGTIFF=YES', vrt_path, cog_local_path
                ], label=f"COG Conversion ({clean_b_name})")

                dest = f"{CONFIG['bucket']}/{mosaic_prefix}/{cog_remote_name}"
                upload(cog_local_path, dest)
                
                if logger: logger(f" Success: {cog_remote_name}", "success")
                results.append(f"gs://{dest}")
            finally:
                if os.path.exists(band_tmp): shutil.rmtree(band_tmp)

    finally:
        if os.path.exists(tmp_path): shutil.rmtree(tmp_path)

    return results


def get_exported_month(exporter):
    """Validate the exact monthly GCS exports recorded by the preceding M1 panel."""
    from M1_mosaic_preview_ui import BANDS, _config_signature
    session = exporter.session
    prepared = session.prepared
    if prepared is None:
        raise ValueError('Prepare y exporte el mosaico en el panel M1 primero.')
    if prepared['signature'] != _config_signature():
        raise ValueError('La configuración cambió. Vuelva a preparar y exportar desde M1.')
    from M0_auth_config import GLOBAL_OPTS
    latest = {row['description']: row for row in session.tasks if row['destination'] == 'GCS'}
    incomplete = []
    for band in BANDS:
        name = mosaic_name(prepared['year'], prepared['month'], 'monthly', band=band,
                           mosaic=prepared['method'], sensor='sentinel2')
        description = f"{GLOBAL_OPTS['PERSONAL_TASK_FLAG']}_GCS_{name}"
        row = latest.get(description)
        state = row['task'].status().get('state', 'UNKNOWN') if row else 'SIN EXPORTAR'
        if state != 'COMPLETED':
            incomplete.append(f'{band}: {state}')
    if incomplete:
        raise ValueError('M2 requiere las siete exportaciones GCS completas. ' + '; '.join(incomplete))
    return {'year': prepared['year'], 'month': prepared['month'], 'period': 'monthly',
            'sensor': 'sentinel2', 'mosaic_method': prepared['method'], 'bands': list(BANDS)}


def assemble_from_export(exporter, logger=None):
    """Assemble all seven completed M1 GCS exports with the existing M2 engine."""
    selection = get_exported_month(exporter)
    prefix = monthly_chunk_path(selection['year'], selection['month'],
                                mosaic=selection['mosaic_method'], sensor=selection['sensor'])
    files = list_gcs_files(prefix, logger=logger)
    missing = []
    for band in selection['bands']:
        name = mosaic_name(selection['year'], selection['month'], 'monthly', band=band,
                           mosaic=selection['mosaic_method'], sensor=selection['sensor'])
        if not any(os.path.basename(file).startswith(name + '_') for file in files):
            missing.append(band)
    if missing:
        raise ValueError('Faltan archivos CHUNKS en GCS para: ' + ', '.join(missing))
    paths = assemble_country_mosaic(**selection, logger=logger)
    if len(paths) != len(selection['bands']):
        raise RuntimeError(f'M2 produjo {len(paths)}/{len(selection["bands"])} COGs. Revise el registro.')
    return paths


M2_BANDS = ('blue', 'green', 'red', 'nir', 'swir1', 'swir2', 'dayOfYear')


def _matches_chunk_name(filename, name):
    # M1's prefix ends in '_'; GEE may append numeric row/column shard coordinates.
    return re.fullmatch(re.escape(name) + r'(?:[_-]+(?:\d+(?:[-_]\d+)*)?)?\.tif', filename) is not None


def scan_gcs_mosaics(years=None):
    """Discover canonical monthly S2 exports directly in GCS, independent of M1 memory."""
    import ee
    root = f"{CONFIG['bucket']}/{CONFIG['gcs_library_images'].strip('/')}"
    try:
        files = _get_fs().find(root + '/SENTINEL2/MONTHLY')
    except FileNotFoundError:
        files = []
    tasks = ee.data.getTaskList()
    selected_years = set(years) if years is not None else None
    methods = set(CONFIG['mosaic_methods'])
    groups = {}
    for file in files:
        path = file.removeprefix('gs://')
        if not path.startswith(root + '/'):
            continue
        parts = path[len(root) + 1:].split('/')
        if len(parts) != 6:
            continue
        sensor, period, method, stamp, kind, filename = parts
        if sensor != 'SENTINEL2' or period != 'MONTHLY' or method.lower() not in methods:
            continue
        if not re.fullmatch(r'\d{4}_(0[1-9]|1[0-2])', stamp) or kind not in ('CHUNKS', 'COG'):
            continue
        year, month = map(int, stamp.split('_'))
        if selected_years is not None and year not in selected_years:
            continue
        for band in M2_BANDS:
            name = mosaic_name(year, month, 'monthly', band=band, sensor='sentinel2', mosaic=method.lower())
            valid = filename == name + '_cog.tif' if kind == 'COG' else _matches_chunk_name(filename, name)
            if not valid:
                continue
            key = f'{stamp}/{method.lower()}'
            row = groups.setdefault(key, dict(key=key, year=year, month=month, method=method.lower(),
                                              chunks=set(), cogs=set(), blocked={}))
            row['chunks' if kind == 'CHUNKS' else 'cogs'].add(band)
            break
    for row in groups.values():
        for band in row['chunks'] - row['cogs']:
            name = mosaic_name(row['year'], row['month'], 'monthly', band=band,
                               sensor='sentinel2', mosaic=row['method'])
            # Flags can differ across sessions; match the stable GCS export suffix.
            matching = [t for t in tasks if str(t.get('description', '')).endswith('_GCS_' + name)]
            if matching:
                latest = max(matching, key=lambda task: task.get('creation_timestamp_ms', 0))
                state = latest.get('state', 'UNKNOWN')
                if any(t.get('state') in ('READY', 'RUNNING', 'PENDING', 'CANCEL_REQUESTED') for t in matching):
                    row['blocked'][band] = 'EN CURSO'
                elif state != 'COMPLETED':
                    row['blocked'][band] = state
        row['missing'] = set(M2_BANDS) - row['chunks'] - row['cogs']
        row['pending'] = row['chunks'] - row['cogs']
        row['ready'] = bool(row['pending']) and not row['missing'] and not row['blocked']
    return sorted(groups.values(), key=lambda row: (row['year'], row['month'], row['method']), reverse=True)


def assemble_gcs_mosaic(key, logger=None):
    """Rescan before processing and assemble only missing COG bands for one GCS group."""
    row = next((row for row in scan_gcs_mosaics() if row['key'] == key), None)
    if row is None:
        raise ValueError('El mosaico ya no aparece en GCS. Actualice la lista.')
    if not row['pending'] and not row['missing']:
        return []
    if not row['ready']:
        raise ValueError('El mosaico tiene bandas ausentes o exportaciones incompletas; actualice al finalizar M1.')
    bands = [band for band in M2_BANDS if band in row['pending']]
    paths = assemble_country_mosaic(row['year'], row['month'], period='monthly', bands=bands,
                                    sensor='sentinel2', mosaic_method=row['method'], logger=logger,
                                    strict_names=True)
    if len(paths) != len(bands):
        raise RuntimeError(f'M2 produjo {len(paths)}/{len(bands)} COGs pendientes. Revise el registro.')
    return paths
