"""Flujo de descarga de Sentinel-2, vista previa y mapa Folium.

Importar este módulo no busca, descarga ni escribe datos. Las funciones reciben
una configuración explícita y devuelven resultados para cuadernos u otros programas.
"""

from dataclasses import dataclass, field, replace
from contextlib import ExitStack, contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, CancelledError
from datetime import date, datetime, timezone, timedelta
from threading import Lock, Event, Thread, BoundedSemaphore
from html import escape
from pathlib import Path
import json
import hashlib
import io
import atexit
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import shutil
import tempfile
import time
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree
from zipfile import ZipFile, is_zipfile

import folium
from branca.element import MacroElement, Template
from folium.plugins import Fullscreen, MousePosition, DualMap
import geopandas as gpd
import matplotlib.image as mpimg
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds, from_origin
from rasterio.windows import Window
from rasterio.shutil import copy as copy_raster
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm.auto import tqdm
from pystac import Item
from pystac_client import Client
from pystac_client.stac_api_io import StacApiIO
from shapely.geometry import shape, box


_ETIQUETAS_ES = {
    "id": "Identificador", "item_id": "Escena", "asset": "Banda o recurso",
    "datetime": "Fecha de adquisición (UTC)", "cloud_cover": "Nubosidad (%)",
    "platform": "Satélite", "processing_datetime": "Fecha de procesamiento (UTC)",
    "title": "Descripción", "type": "Tipo de archivo", "resolution_m": "Resolución (m)",
    "url": "URL", "bytes": "Tamaño (bytes)", "crs": "Sistema de referencia",
    "resolution": "Resolución (m)", "path": "Ruta", "Name": "Tesela", "geometry": "Geometría",
    "nombre": "Nombre", "macroregio": "Macrorregión", "regimen_fu": "Régimen del fuego",
    "prioridad": "Prioridad", "LEVEL_1": "País", "LEVEL_2": "Macrorregión", "LEVEL_3": "Región",
    "id_monitor": "ID de región", "tile": "Tesela", "download_bytes": "Descarga pendiente (bytes)",
    "status": "Estado", "download_s": "Descarga (s)", "wait_s": "Espera descarga (s)",
    "mask_s": "Máscara y calibración (s)", "resample_s": "Lectura y remuestreo (s)",
    "indices_s": "Índices (s)", "mosaic_s": "Composición (s)", "cleanup_s": "Limpieza (s)",
    "process_s": "Procesamiento (s)", "scene_elapsed_s": "Tiempo de escena (s)", "error": "Error",
    "download_started_utc": "Inicio descarga (UTC)", "download_finished_utc": "Fin descarga (UTC)",
    "processing_started_utc": "Inicio procesamiento (UTC)", "processing_finished_utc": "Fin procesamiento (UTC)",
}

_DESCRIPCIONES_RECURSOS_ES = {
    "aot": "Espesor óptico de aerosoles", "blue": "Azul — B02, 10 m",
    "cloud": "Probabilidad de nubes", "coastal": "Banda costera — B01, 60 m",
    "granule_metadata": "Metadatos del gránulo", "green": "Verde — B03, 10 m",
    "nir": "Infrarrojo cercano — B08, 10 m", "nir08": "Infrarrojo cercano — B8A, 20 m",
    "nir09": "Vapor de agua — B09, 60 m", "product_metadata": "Metadatos del producto",
    "red": "Rojo — B04, 10 m", "rededge1": "Borde rojo — B05, 20 m",
    "rededge2": "Borde rojo — B06, 20 m", "rededge3": "Borde rojo — B07, 20 m",
    "scl": "SCL — clases por píxel, nubes y sombras (20 m)", "snow": "Probabilidad de nieve",
    "swir16": "Infrarrojo de onda corta — B11, 20 m",
    "swir22": "Infrarrojo de onda corta — B12, 20 m",
    "tileinfo_metadata": "Metadatos de la tesela", "visual": "Imagen en color natural",
    "wvp": "Vapor de agua", "thumbnail": "Miniatura de la vista previa",
}


def format_table(tabla: pd.DataFrame) -> pd.DataFrame:
    """Prepara una tabla de visualización sin modificar los datos ni sus claves originales."""
    vista = pd.DataFrame(tabla).copy()
    if "asset" in vista and "title" in vista:
        def descripcion(clave):
            clave = str(clave)
            texto = _DESCRIPCIONES_RECURSOS_ES.get(clave.removesuffix("-jp2"), "Archivo auxiliar")
            return texto + (" (JPEG 2000)" if clave.endswith("-jp2") else "")
        vista["title"] = vista["asset"].map(descripcion)
    return vista.rename(columns=_ETIQUETAS_ES)


@dataclass
class PipelineConfig:
    """Parámetros editables; output_dir organiza los resultados por región o tesela."""

    shapefile: Path
    regions_zip: Path | None = None
    regions_layer: str | None = None
    region_fields: tuple[str, ...] = ()
    region_color_field: str | None = None
    region_id: int | str | None = None
    region_id_field: str = "id_monitor"
    region_name_field: str = "nombre"
    tile_field: str = "Name"
    tile_name: str = "18NZL"
    start_date: str = "2025-10-16"
    end_date: str = "2026-10-16"
    max_cloud_cover: float = 20
    sort_by: str = "cloud_cover"
    bands: tuple[str, ...] = ("blue", "green", "red", "nir")
    item_ids: tuple[str, ...] = ()
    download_images: bool = True
    download_workers: int = 3
    stac_url: str = "https://earth-search.aws.element84.com/v1"
    collection: str = "sentinel-2-l2a"
    data_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent / "data")
    map_max_pixels: int = 1536
    map_simplify_meters: float = 100
    map_scene_ids: tuple[str, ...] = ()
    map_preview_remote: bool = False
    map_show_all: bool = False
    map_include_rgb: bool = True
    map_bands: tuple[str, str, str] = ("red", "green", "blue")
    map_min: float | None = None
    map_max: float | None = None
    source: str = "earth-search"
    workflow_parameters: dict | None = None

    def __post_init__(self):
        if self.source not in {"earth-search", "planetary-computer"}:
            raise ValueError("La fuente debe ser earth-search o planetary-computer.")
        if self.source == "planetary-computer":
            self.stac_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
            if self.collection != "sentinel-2-l2a":
                raise ValueError("Planetary Computer requiere la colección sentinel-2-l2a.")
        self.shapefile = Path(self.shapefile)
        self.data_dir = Path(self.data_dir)
        if self.regions_zip is not None:
            self.regions_zip = Path(self.regions_zip)

    @property
    def output_dir(self) -> Path:
        if self.region_id is not None:
            identificador = re.sub(r"[^\w-]", "_", str(self.region_id))
            return self.data_dir / f"region_{identificador}"
        return self.data_dir / self.tile_name


@dataclass
class SearchResult:
    items: list[Item]
    scenes: pd.DataFrame
    selected: list[Item]


@dataclass
class DownloadResult:
    paths: dict[str, dict[str, Path]]
    manifest: list[dict]

    @property
    def table(self) -> pd.DataFrame:
        return pd.DataFrame(self.manifest)


@dataclass
class RGBPreview:
    image: np.ndarray
    path: Path
    title: str


@dataclass
class MapResult:
    map: folium.Map
    path: Path
    intersecting_regions: pd.DataFrame
    overlays: list[dict]
    region_count: int


@dataclass
class RegionSelection:
    region: gpd.GeoDataFrame
    tiles: gpd.GeoDataFrame
    name: str


FIRE_REQUIRED_ASSETS = ("red", "nir", "swir16", "swir22", "scl")
FIRE_COMPOSITE_BANDS = ("red", "nir", "swir1", "swir2", "NDVI", "NBR", "nbr", "month", "time", "scene_index")


@dataclass
class FireCompositeResult:
    path: Path
    metadata_path: Path
    valid_pixels: int
    scene_count: int
    timings_path: Path | None = None
    timing_summary_path: Path | None = None


def descargar_vector_drive(file_id: str, destino: Path, autenticar: bool = False) -> Path:
    """Obtiene un ZIP de Drive; para archivos privados permite autenticar en Colab."""
    destino = Path(destino)
    referencia = destino.with_suffix(destino.suffix + ".drive_id")
    if destino.is_file() and is_zipfile(destino) and referencia.is_file() and referencia.read_text() == file_id:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(".zip.part")
    try:
        if autenticar:
            from google.colab import auth
            import google.auth
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
            auth.authenticate_user()
            credenciales, _ = google.auth.default()
            servicio = build("drive", "v3", credentials=credenciales, cache_discovery=False)
            solicitud = servicio.files().get_media(fileId=file_id, supportsAllDrives=True)
            with parcial.open("wb") as archivo:
                descarga = MediaIoBaseDownload(archivo, solicitud)
                terminado = False
                while not terminado:
                    _, terminado = descarga.next_chunk()
        else:
            import gdown
            if not gdown.download(id=file_id, output=str(parcial), quiet=True):
                raise RuntimeError("Drive no devolvió el archivo solicitado.")
        if not is_zipfile(parcial):
            raise ValueError("El archivo obtenido no es un ZIP de datos vectoriales.")
        parcial.replace(destino)
        referencia.write_text(file_id)
        return destino
    except Exception as error:
        raise RuntimeError(
            f"No se pudo obtener el ZIP de Drive con ID {file_id}. "
            "Compruebe sus permisos; para archivos privados active AUTENTICAR_DRIVE en Colab."
        ) from error


def load_regions(config: PipelineConfig) -> gpd.GeoDataFrame:
    """Carga las regiones y comprueba los campos para seleccionar una por ID."""
    if config.regions_zip is None or not config.regions_zip.is_file():
        raise FileNotFoundError(f"No se encontró el ZIP de regiones: {config.regions_zip}")
    regiones = _read_vector(config.regions_zip, layer=config.regions_layer)
    if regiones.crs is None or regiones.empty:
        raise ValueError("Las regiones deben tener geometrías y sistema de referencia.")
    if regiones.geometry.isna().any() or regiones.geometry.is_empty.any():
        raise ValueError("Hay regiones sin geometría.")
    if not regiones.is_valid.all():
        regiones.geometry = regiones.geometry.make_valid()
    # Admite el ZIP de monitoreo (26 regiones) y el archivo anterior (13 regiones).
    # Las columnas normalizadas se crean en memoria; los shapefiles no se modifican.
    if config.region_name_field not in regiones and config.region_name_field == "nombre" and "LEVEL_3" in regiones:
        regiones["nombre"] = regiones["LEVEL_3"]
    if config.region_id_field not in regiones and config.region_id_field == "id_monitor":
        regiones["id_monitor"] = range(1, len(regiones) + 1)
    if "macroregio" not in regiones and "LEVEL_2" in regiones:
        regiones["macroregio"] = regiones["LEVEL_2"]
    for campo in ["regimen_fu", "prioridad"]:
        if campo not in regiones:
            regiones[campo] = "Sin información"
    for campo in [config.region_id_field, config.region_name_field]:
        if campo not in regiones:
            raise ValueError(f"Falta el campo de regiones {campo!r}.")
    if regiones[config.region_id_field].isna().any() or regiones[config.region_id_field].duplicated().any():
        raise ValueError("Cada región debe tener un ID no vacío y único.")
    return regiones.to_crs(4326)


def list_regions(config: PipelineConfig) -> pd.DataFrame:
    """Devuelve el listado de IDs, nombres y atributos de las regiones disponibles."""
    regiones = load_regions(config)
    campos = list(dict.fromkeys([config.region_id_field, config.region_name_field, "macroregio", "prioridad"]))
    return pd.DataFrame(regiones[[c for c in campos if c in regiones]]).sort_values(config.region_id_field).reset_index(drop=True)


def select_region(config: PipelineConfig) -> RegionSelection:
    """Selecciona una región y determina todas las teselas que la intersectan."""
    if config.region_id is None:
        raise ValueError("Seleccione un ID de región antes de continuar.")
    regiones = load_regions(config)
    region = regiones.loc[regiones[config.region_id_field].astype(str) == str(config.region_id)].copy()
    if len(region) != 1:
        raise ValueError(f"El ID {config.region_id!r} no está en la lista de regiones disponibles.")
    indice = _read_vector(config.shapefile)
    if indice.crs is None or config.tile_field not in indice:
        raise ValueError("El índice de teselas debe incluir sistema de referencia y campo de identificación.")
    indice = indice.to_crs(4326)
    geometria = region.geometry.iloc[0]
    candidatas = indice.loc[indice.intersects(geometria)].copy()
    teselas = candidatas.loc[candidatas.geometry.map(lambda poligono: poligono.intersection(geometria).area > 0)].copy()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    region.to_file(config.output_dir / "region_boundary.geojson", driver="GeoJSON")
    teselas.to_file(config.output_dir / "tiles_intersecting.geojson", driver="GeoJSON")
    nombre = str(region.iloc[0][config.region_name_field])
    print(f"Región seleccionada: {nombre}. Teselas que la intersectan: {len(teselas)}.")
    return RegionSelection(region=region, tiles=teselas, name=nombre)


def _retry_policy() -> Retry:
    return Retry(total=4, backoff_factor=1,
                 status_forcelist=[429, 500, 502, 503, 504],
                 allowed_methods=frozenset(["GET", "HEAD", "POST"]))


def _read_vector(path: Path, layer: str | None = None, **kwargs) -> gpd.GeoDataFrame:
    """Lee un archivo vectorial o un shapefile específico dentro de un ZIP.

    Seleccionar un archivo evita ambigüedades cuando una exportación de Drive
    contiene un shapefile y un GeoPackage. El ZIP se lee sin extraerlo.
    """
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            members = [
                name for name in archive.namelist()
                if name.lower().endswith(".shp") and not name.startswith("__MACOSX/")
            ]
        if layer is not None:
            members = [name for name in members if Path(name).stem.casefold() == layer.casefold()]
        if len(members) != 1:
            raise ValueError(
                f"Se esperaba un shapefile en {path.name} para la capa {layer!r}; "
                f"se encontraron {members}. Indique el nombre del shapefile sin extensión."
            )
        return gpd.read_file(f"zip://{path.resolve()}!{members[0]}", **kwargs)
    if layer is not None and path.suffix.lower() != ".shp":
        kwargs["layer"] = layer
    return gpd.read_file(path, **kwargs)


def _require_l2a(item: Item) -> None:
    """Rechaza productos que no estén identificados como Sentinel-2 L2A."""
    if item.properties.get("s2:product_type") != "S2MSI2A":
        raise ValueError(f"{item.id} no está identificado como reflectancia de superficie Sentinel-2 de Nivel 2A (L2A).")


def load_tile(config: PipelineConfig) -> gpd.GeoDataFrame:
    """Selecciona, valida y exporta la tesela solicitada en WGS84."""
    if not config.shapefile.is_file():
        raise FileNotFoundError(f"No se encontró el shapefile: {config.shapefile}")
    if not re.fullmatch(r"\d{2}[C-HJ-NP-X][A-HJ-NP-Z]{2}", config.tile_name):
        raise ValueError("Use un identificador de tesela MGRS de Sentinel-2, por ejemplo 18NZL.")
    if date.fromisoformat(config.start_date) > date.fromisoformat(config.end_date):
        raise ValueError("config.start_date debe ser anterior o igual a config.end_date.")
    if not 0 <= config.max_cloud_cover <= 100:
        raise ValueError("Use una nubosidad de 0 a 100.")
    if config.sort_by not in {"cloud_cover", "latest"} or not config.bands or len(set(config.bands)) != len(config.bands):
        raise ValueError("Revise config.sort_by e indique bandas sin duplicados en config.bands.")

    # El filtro SQL evita cargar en memoria el índice mundial completo de teselas.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.tile_field):
        raise ValueError("config.tile_field debe ser un nombre de campo válido del shapefile.")
    tile = _read_vector(config.shapefile, where=f'"{config.tile_field}" = \'{config.tile_name}\'')
    if len(tile) != 1:
        raise ValueError(f"Se esperaba una entidad con {config.tile_field}={config.tile_name}; se encontraron {len(tile)}.")
    if tile.crs is None:
        raise ValueError("El shapefile no tiene sistema de referencia. Restaure el archivo .prj antes de buscar.")
    tile = tile.to_crs("EPSG:4326")
    geometry = tile.geometry.iloc[0]
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("La geometría de la tesela seleccionada está vacía o no es válida.")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    tile.to_file(config.output_dir / "tile_boundary.geojson", driver="GeoJSON")

    return tile


def _item_tile(item: Item) -> str:
    """Lee el identificador MGRS de la tesela desde las propiedades STAC."""
    p = item.properties
    if p.get("s2:mgrs_tile"):
        return str(p["s2:mgrs_tile"])
    return f"{int(p['mgrs:utm_zone']):02d}{p['mgrs:latitude_band']}{p['mgrs:grid_square']}"


# Los nombres internos se mantienen iguales para ambos catálogos.
_PC_BANDS = {
    "B01": "coastal", "B02": "blue", "B03": "green", "B04": "red",
    "B05": "rededge1", "B06": "rededge2", "B07": "rededge3", "B08": "nir",
    "B8A": "nir08", "B09": "nir09", "B11": "swir16", "B12": "swir22",
    "SCL": "scl", "AOT": "aot", "WVP": "wvp",
}
_S2_XML_BANDS = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B10", "B11", "B12")


def normalize_gee_parameters(parameters: dict) -> dict:
    """Valida una copia de los parámetros compartidos; la fecha final es exclusiva."""
    params = json.loads(json.dumps(parameters))
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    if start >= end:
        raise ValueError("La fecha final exclusiva debe ser posterior a la inicial.")
    if not 0 <= params["max_cloud_cover"] <= 100:
        raise ValueError("La nubosidad debe estar entre 0 y 100.")
    if not np.isfinite(params["resolution_m"]) or params["resolution_m"] <= 0:
        raise ValueError("La resolución debe ser positiva y finita.")
    canonical = []
    for band in params["bands"]:
        band = str(band).upper()
        if re.fullmatch(r"B[1-9]", band):
            band = "B0" + band[1:]
        if band not in _PC_BANDS or not band.startswith("B"):
            raise ValueError(f"Banda espectral no compatible: {band}.")
        canonical.append(_PC_BANDS[band])
    # Los índices siempre requieren estas cuatro bandas; SCL se descarga aparte.
    canonical = list(dict.fromkeys([*canonical, *FIRE_REQUIRED_ASSETS[:-1]]))
    inverse = {value: key for key, value in _PC_BANDS.items()}
    params["bands"] = [inverse[b].replace("B0", "B") for b in canonical]
    params["download_bands"] = [*canonical, "scl"]
    if not params.get("scl_exclude") or any(not isinstance(v, int) or not 0 <= v <= 11 for v in params["scl_exclude"]):
        raise ValueError("Indique las clases SCL que se excluirán, entre 0 y 11.")
    params["scl_exclude"] = sorted(set(params["scl_exclude"]) | {0})
    vis = params["vis"]
    if len(vis["bands"]) != 3 or not set(vis["bands"]) <= {"red", "nir", "swir1", "swir2"}:
        raise ValueError("La visualización usa tres canales entre red, nir, swir1 y swir2.")
    if not np.isfinite([vis["min"], vis["max"]]).all() or vis["min"] >= vis["max"]:
        raise ValueError("Revise el mínimo y máximo de visualización.")
    return params


def config_from_gee_parameters(parameters: dict, *, shapefile: Path, regions_zip: Path,
                               data_dir: Path, source: str = "earth-search") -> PipelineConfig:
    """Convierte el intervalo GEE [inicio, fin) en fechas STAC incluidas."""
    params = normalize_gee_parameters(parameters)
    return PipelineConfig(
        shapefile=shapefile, regions_zip=regions_zip, region_id=params["region_id"], source=source,
        start_date=params["start_date"], end_date=(date.fromisoformat(params["end_date"]) - timedelta(days=1)).isoformat(),
        max_cloud_cover=params["max_cloud_cover"], bands=tuple(params["download_bands"]),
        data_dir=Path(data_dir) / source / f"{params['start_date']}_{params['end_date']}",
        download_images=False, map_include_rgb=False, workflow_parameters=params,
    )


def build_gee_mosaic(parameters: dict, *, include_metadata: bool = False):
    """Construye la vista GEE con máscara y calidad compartidas; no descarga escenas.

    Requiere Earth Engine autenticado. Se importa de forma diferida para que el
    flujo de descarga siga funcionando sin el SDK ni credenciales de GEE.
    """
    import ee
    params = normalize_gee_parameters(parameters)
    regions = ee.FeatureCollection(params["regions_asset"]).filter(ee.Filter.eq("id_monitor", params["region_id"]))
    geometry = regions.geometry()
    collection = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                  .filterBounds(geometry).filterDate(params["start_date"], params["end_date"])
                  .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", params["max_cloud_cover"]))
                  .sort("system:index"))
    names = [{"swir16": "swir1", "swir22": "swir2"}.get(b, b)
             for b in params["download_bands"] if b != "scl"]
    def prepare(image):
        scl = image.select("SCL")
        mask = scl.remap(params["scl_exclude"], [0] * len(params["scl_exclude"]), 1).eq(1)
        scaled = image.select(params["bands"], names).multiply(0.0001).updateMask(mask)
        positive = scaled.select(["red", "nir", "swir1", "swir2"]).gt(0).reduce(ee.Reducer.min())
        scaled = scaled.updateMask(positive)
        nbr = scaled.normalizedDifference(["nir", "swir2"]).rename("NBR")
        quality = nbr.add(1).multiply(-1000).toInt16().rename("nbr")
        return (scaled.addBands([nbr, quality]).copyProperties(image, ["system:time_start", "system:index"])
                .set("date", image.date().format("YYYY-MM-dd")))
    prepared = collection.map(prepare)
    mosaic = prepared.qualityMosaic("nbr").clip(geometry)
    result = {"mosaic": mosaic, "regions": regions,
            "tiles": ee.FeatureCollection(params["tiles_asset"]).filterBounds(geometry),
            "count": None, "dates": None,
            "parameters": params}
    if include_metadata:
        stats = ee.Dictionary({"region_count": regions.size(), "count": collection.size(),
                               "dates": prepared.aggregate_array("date").distinct().sort()}).getInfo()
        if stats["region_count"] != 1:
            raise ValueError("El asset GEE debe contener exactamente una región con ese ID.")
        if not stats["count"]:
            raise ValueError("No hay imágenes en GEE con los parámetros elegidos.")
        result.update(count=stats["count"], dates=stats["dates"])
    return result


def _normalize_pc_item(item: Item) -> Item:
    """Copia el ítem y normaliza recursos; no lee bandas ni solicita tokens."""
    item = item.clone()
    _require_l2a(item)
    for original, canonical in _PC_BANDS.items():
        if original in item.assets:
            asset = item.assets.pop(original)
            asset.extra_fields["pipeline:original_asset"] = original
            item.add_asset(canonical, asset)
    item.properties["pipeline:source"] = "planetary-computer"
    return item


def _prepare_pc_item(item: Item, session: requests.Session) -> None:
    """Renueva SAS por escena y obtiene calibración del XML oficial cuando falta.

    No infiere desplazamientos por fecha: los productos antiguos pueden haber
    sido reprocesados con otra línea base. No modifica los valores del archivo.
    """
    import planetary_computer
    for asset in item.assets.values():
        parts = urlsplit(asset.href)
        # El SDK debe recibir la URL original para no reutilizar un SAS vencido.
        asset.href = planetary_computer.sign(urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")))
    spectral = [(original, canonical) for original, canonical in _PC_BANDS.items()
                if original.startswith("B") and canonical in item.assets]
    missing = [(original, canonical) for original, canonical in spectral
               if not {"scale", "offset"}.issubset((item.assets[canonical].extra_fields.get("raster:bands") or [{}])[0])]
    if not missing:
        return
    if "product-metadata" not in item.assets:
        raise ValueError(f"Falta el XML de calibración de Planetary Computer para {item.id}.")
    with session.get(item.assets["product-metadata"].href, timeout=(30, 60)) as response:
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    values = {}
    offsets = {}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"BOA_QUANTIFICATION_VALUE", "PROCESSING_BASELINE"}:
            values[tag] = float(node.text)
        elif tag == "BOA_ADD_OFFSET":
            offsets[int(node.attrib["band_id"])] = float(node.text)
    quantification = values.get("BOA_QUANTIFICATION_VALUE", 0)
    baseline = values.get("PROCESSING_BASELINE", float(item.properties.get("s2:processing_baseline", "nan")))
    if not np.isfinite(quantification) or quantification <= 0:
        raise ValueError(f"Cuantificación BOA inválida o ausente en {item.id}.")
    for original, canonical in missing:
        index = _S2_XML_BANDS.index(original)
        if index not in offsets and (not np.isfinite(baseline) or baseline >= 4):
            raise ValueError(f"Falta BOA_ADD_OFFSET para {item.id}/{original}.")
        metadata = (item.assets[canonical].extra_fields.get("raster:bands") or [{}])[0].copy()
        metadata.update(scale=1 / quantification, offset=offsets.get(index, 0) / quantification, nodata=0)
        item.assets[canonical].extra_fields["raster:bands"] = [metadata]
    item.properties["pipeline:calibration"] = "product-metadata/BOA_QUANTIFICATION_VALUE y BOA_ADD_OFFSET"


def _item_datetime(item: Item) -> str:
    """Obtiene la fecha tanto de un ítem deserializado como de uno creado en Python."""
    if item.properties.get("datetime"):
        return item.properties["datetime"]
    if item.datetime is None:
        raise ValueError(f"La escena {item.id} no tiene fecha de adquisición.")
    return item.datetime.isoformat().replace("+00:00", "Z")


def search_scenes(config: PipelineConfig, tile: gpd.GeoDataFrame) -> SearchResult:
    """Busca escenas, elimina versiones duplicadas de procesamiento y ordena la selección."""
    if date.fromisoformat(config.start_date) > date.fromisoformat(config.end_date):
        raise ValueError("La fecha inicial debe ser anterior o igual a la fecha final.")
    if not 0 <= config.max_cloud_cover <= 100:
        raise ValueError("Indique nubosidad entre 0 y 100.")
    if config.collection not in {"sentinel-2-l2a", "sentinel-2-c1-l2a"}:
        raise ValueError("Use una colección Sentinel-2 L2A para obtener reflectancia de superficie.")
    retry = _retry_policy()
    geometry = tile.geometry.iloc[0]
    catalog = Client.open(config.stac_url, stac_io=StacApiIO(timeout=60, max_retries=retry))
    consulta = {"eo:cloud_cover": {"lte": config.max_cloud_cover}}
    if config.region_id is None:
        consulta.update({"s2:mgrs_tile": {"eq": config.tile_name}} if config.source == "planetary-computer" else {
            "mgrs:utm_zone": {"eq": int(config.tile_name[:2])},
            "mgrs:latitude_band": {"eq": config.tile_name[2]},
            "mgrs:grid_square": {"eq": config.tile_name[3:]},
        })
    search = catalog.search(
        collections=[config.collection],
        intersects=geometry.__geo_interface__,
        datetime=f"{config.start_date}T00:00:00Z/{config.end_date}T23:59:59.999999Z",
        query=consulta,
        limit=100,  # tamaño de página; la paginación recupera todas las coincidencias
    )
    items = list(search.items())
    if config.source == "planetary-computer":
        items = [_normalize_pc_item(item) for item in items]
    if config.region_id is not None:
        items = [item for item in items if item.geometry and shape(item.geometry).intersection(geometry).area > 0]
    if not items:
        if config.region_id is not None:
            columnas = ["id", "tile", "datetime", "cloud_cover", "platform", "processing_datetime"]
            vacio = pd.DataFrame(columns=columnas)
            vacio.to_csv(config.output_dir / "search_results.csv", index=False)
            (config.output_dir / "available_items.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
            print("No hay escenas coincidentes; amplíe las fechas o el umbral de nubosidad.")
            return SearchResult(items=[], scenes=vacio, selected=[])
        raise RuntimeError("No hay escenas coincidentes. Amplíe el intervalo de fechas o config.max_cloud_cover.")

    if config.region_id is None and any(_item_tile(item) != config.tile_name for item in items):
        raise ValueError("El catálogo devolvió una escena de otra tesela MGRS.")
    for item in items:
        _require_l2a(item)
    rows = [{
        "id": item.id,
        "tile": _item_tile(item),
        "datetime": _item_datetime(item),
        "cloud_cover": item.properties["eo:cloud_cover"],
        "platform": item.properties.get("platform", ""),
        "processing_datetime": item.properties.get("s2:generation_time", item.properties.get("updated", "")),
    } for item in items]
    scenes = pd.DataFrame(rows).sort_values(["processing_datetime", "id"], ascending=False)
    scenes = scenes.drop_duplicates(["tile", "platform", "datetime"])
    sort_columns = ["cloud_cover", "datetime"] if config.sort_by == "cloud_cover" else ["datetime", "cloud_cover"]
    ascending = [True, False] if config.sort_by == "cloud_cover" else [False, True]
    scenes = scenes.sort_values(sort_columns, ascending=ascending).reset_index(drop=True)
    scenes.to_csv(config.output_dir / "search_results.csv", index=False)
    (config.output_dir / "available_items.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": [item.to_dict() for item in items]
    }))

    by_id = {item.id: item for item in items}
    if config.item_ids:
        missing = set(config.item_ids) - by_id.keys()
        if missing:
            raise ValueError(f"Estos config.item_ids no aparecen en la búsqueda actual: {sorted(missing)}")
        selected = [by_id[item_id] for item_id in dict.fromkeys(config.item_ids)]
    else:
        selected = [by_id[item_id] for item_id in scenes["id"]]
    return SearchResult(items=items, scenes=scenes, selected=selected)


def available_bands(items: list[Item]) -> dict[str, str]:
    """Lista bandas y capas auxiliares COG disponibles en todas las escenas."""
    if not items:
        return {}
    recursos = [
        {key for key, asset in item.assets.items()
         if "tiff" in (asset.media_type or "").lower()
         and "visual" not in (asset.roles or []) and key != "visual"}
        for item in items
    ]
    comunes = set.intersection(*recursos)
    def orden(key):
        bandas = items[0].assets[key].extra_fields.get("eo:bands", [])
        return (bandas[0].get("center_wavelength", float("inf")) if bandas else float("inf"), key)
    return {key: _DESCRIPCIONES_RECURSOS_ES.get(key, items[0].assets[key].title or key)
            for key in sorted(comunes, key=orden)}


def asset_table(item: Item) -> pd.DataFrame:
    """Describe las bandas y los archivos disponibles de una escena."""
    return pd.DataFrame([
        {"asset": key, "title": asset.title, "type": asset.media_type,
         "resolution_m": asset.extra_fields.get("gsd", item.properties.get("gsd")),
         "url": asset.href}
        for key, asset in item.assets.items()
    ])


def _check_raster(path: Path) -> None:
    """Comprueba que el archivo descargado sea un ráster georreferenciado y legible."""
    with rasterio.open(path) as src:
        if src.crs is None or src.width < 1 or src.height < 1 or src.count < 1:
            raise ValueError(f"Falta información geométrica del ráster en {path}")
        src.read(1, window=((0, min(32, src.height)), (0, min(32, src.width))))


def download_asset(url: str, destination: Path, session: requests.Session, *,
                   expected_size: int | None = None, show_progress: bool = True, cancel_event=None) -> Path:
    """Descarga un COG público y reutiliza archivos completos validados; el cambio de nombre es atómico."""
    if not url.startswith("https://"):
        raise ValueError(f"Se esperaba la URL HTTPS pública de un COG; se recibió {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if expected_size and destination.is_file() and destination.stat().st_size == expected_size:
        try:
            _check_raster(destination)
            return destination
        except (rasterio.errors.RasterioIOError, ValueError):
            pass  # Un archivo incompleto o corrupto se vuelve a descargar.
    for attempt in range(1, 4):
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("Descarga cancelada.")
            with session.get(url, stream=True, timeout=(30, 120),
                          headers={"Accept-Encoding": "identity"}) as response:
                response.raise_for_status()
                transfer_size = int(response.headers.get("Content-Length", 0)) or expected_size or 0
                with partial.open("wb") as out, tqdm(
                    total=transfer_size or None, unit="B", unit_scale=True,
                    desc=f"Descargando {destination.name}", mininterval=1, disable=not show_progress,
                ) as progress:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if cancel_event is not None and cancel_event.is_set():
                            raise CancelledError("Descarga cancelada.")
                        if chunk:
                            out.write(chunk)
                            progress.update(len(chunk))
            if transfer_size and partial.stat().st_size != transfer_size:
                raise OSError(f"Descarga incompleta: {partial}")
            _check_raster(partial)
            partial.replace(destination)
            return destination
        except (requests.RequestException, OSError, ValueError):
            if attempt == 3:
                raise
            if show_progress:
                print(f"Se reintenta {destination.name} (intento {attempt + 1}/3)")
            time.sleep(2 ** attempt)


def plan_download(config: PipelineConfig, selected: list[Item], session=None, *, reserve_bytes: int = 0) -> pd.DataFrame:
    """Consulta tamaños sin descargar bandas y comprueba el espacio libre real."""
    propio = session is None
    session = session or requests.Session()
    registros = []
    try:
        for item in selected:
            _require_l2a(item)
            if config.source == "planetary-computer":
                _prepare_pc_item(item, session)
            for banda in config.bands:
                asset = item.assets[banda]
                with session.head(asset.href, timeout=(30, 60), allow_redirects=True) as respuesta:
                    respuesta.raise_for_status()
                    tamano = int(respuesta.headers.get("Content-Length", 0))
                if tamano <= 0:
                    raise ValueError(f"No se pudo determinar el tamaño de {item.id}/{banda}; no se iniciará la descarga.")
                destino = config.output_dir / item.id / f"{banda}.tif"
                reutilizable = destino.is_file() and destino.stat().st_size == tamano
                registros.append({"item_id": item.id, "asset": banda, "bytes": tamano,
                                  "download_bytes": 0 if reutilizable else tamano})
        tabla = pd.DataFrame(registros, columns=["item_id", "asset", "bytes", "download_bytes"])
        pendiente = int(tabla["download_bytes"].sum()) if len(tabla) else 0
        config.output_dir.mkdir(parents=True, exist_ok=True)
        if pendiente + reserve_bytes + 256 * 1024**2 > shutil.disk_usage(config.output_dir).free:
            raise ValueError("No hay espacio temporal suficiente para las bandas y un margen de 256 MB.")
        return tabla
    finally:
        if propio:
            session.close()


def download_scenes(config: PipelineConfig, selected: list[Item], *, reserve_bytes: int = 0,
                    quiet: bool = False, on_progress=None, cancel_event=None) -> DownloadResult:
    """Descarga las bandas nativas seleccionadas y guarda sus metadatos y registro."""
    if not config.bands:
        raise ValueError("Marque al menos una banda antes de descargar.")
    if not isinstance(config.download_workers, int) or not 1 <= config.download_workers <= 8:
        raise ValueError("Use entre 1 y 8 descargas simultáneas en download_workers.")
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=_retry_policy()))
    try:
        # Valida los archivos seleccionados antes de iniciar las transferencias.
        for item in selected:
            _require_l2a(item)
            for band in config.bands:
                if band not in item.assets:
                    raise ValueError(f"No existe el recurso {band!r} en {item.id}. Disponibles: {list(item.assets)}")
                if not item.assets[band].href.startswith("https://"):
                    raise ValueError(f"{band} no es un recurso HTTPS público; elija su versión COG.")
                if "tiff" not in (item.assets[band].media_type or "").lower():
                    raise ValueError(f"{band!r} no es una banda GeoTIFF. Elija una versión COG de la tabla.")

        downloaded = {}
        manifest = []
        if config.download_images:
            plan = plan_download(config, selected, session=session, reserve_bytes=reserve_bytes)
            sizes = {(r.item_id, r.asset): int(r.bytes) for r in plan.itertuples()}
            if not quiet:
                print(f"Volumen pendiente: {plan['download_bytes'].sum() / 1e9:.2f} GB.")
            for item in selected:
                scene_dir = config.output_dir / item.id
                scene_dir.mkdir(parents=True, exist_ok=True)
                (scene_dir / "item.json").write_text(json.dumps(item.to_dict(), indent=2))
                downloaded[item.id] = {}

                def transfer(band):
                    # Cada transferencia tiene su sesión; no se comparte Session entre hilos.
                    with requests.Session() as worker_session:
                        worker_session.mount("https://", HTTPAdapter(max_retries=_retry_policy()))
                        return download_asset(item.assets[band].href, scene_dir / f"{band}.tif", worker_session,
                                              expected_size=sizes[(item.id, band)], show_progress=False, cancel_event=cancel_event)

                with ThreadPoolExecutor(max_workers=config.download_workers) as pool, tqdm(
                    total=len(config.bands), desc=f"Descarga {item.id}", unit="banda", disable=quiet,
                ) as progress:
                    futures = {pool.submit(transfer, band): band for band in config.bands}
                    try:
                        for future in as_completed(futures):
                            band = futures[future]
                            path = future.result()
                            asset = item.assets[band]
                            downloaded[item.id][band] = path
                            with rasterio.open(path) as src:
                                record = {
                                    "item_id": item.id, "datetime": _item_datetime(item),
                                    "tile": _item_tile(item), "cloud_cover": item.properties["eo:cloud_cover"],
                                    "region_id": config.region_id,
                                    "processing_level": "L2A",
                                    "asset": band, "source_url": asset.href,
                                    "path": str(path.relative_to(config.output_dir)), "bytes": path.stat().st_size,
                                    "crs": str(src.crs), "shape": [src.height, src.width],
                                    "resolution": list(src.res), "dtype": src.dtypes[0], "nodata": src.nodata,
                                    "raster_bands": asset.extra_fields.get("raster:bands", []),
                                }
                            manifest.append(record)
                            # El registro solo se escribe desde el hilo principal.
                            (config.output_dir / "download_manifest.json").write_text(json.dumps(manifest, indent=2))
                            progress.update(1)
                            if on_progress is not None:
                                on_progress(len(downloaded[item.id]), len(config.bands))
                    except BaseException:
                        if cancel_event is not None:
                            cancel_event.set()
                        for future in futures:
                            future.cancel()
                        raise
            (config.output_dir / "selected_items.geojson").write_text(json.dumps({
                "type": "FeatureCollection", "features": [item.to_dict() for item in selected],
            }, indent=2))
            if not quiet:
                print(f"Se descargaron o reutilizaron {len(manifest)} archivos; {sum(r['bytes'] for r in manifest)/1e6:.1f} MB en total")
        else:
            if not quiet:
                print("Solo búsqueda. Establezca config.download_images = True y ejecute esta celda para descargar.")
    finally:
        session.close()
    return DownloadResult(paths=downloaded, manifest=manifest)


def create_rgb_preview(config: PipelineConfig, first: Item, paths: dict[str, Path]) -> RGBPreview | None:
    """Guarda una vista RGB con ajuste de contraste; devuelve None si faltan bandas RGB."""
    rgb_bands = ["red", "green", "blue"]
    if all(band in paths for band in rgb_bands):
        channels = []
        masks = []
        grid = None
        for band in rgb_bands:
            with rasterio.open(paths[band]) as src:
                this_grid = (src.crs, src.transform, src.width, src.height)
                if grid is not None and this_grid != grid:
                    raise ValueError("Las bandas RGB tienen rejillas distintas; alinéelas antes de generar la vista previa.")
                grid = this_grid
                height = max(1, round(src.height * min(1, 1024 / max(src.height, src.width))))
                width = max(1, round(src.width * min(1, 1024 / max(src.height, src.width))))
                values = src.read(1, out_shape=(height, width), masked=True,
                                  resampling=Resampling.average)
            metadata = first.assets[band].extra_fields.get("raster:bands", [{}])[0]
            reflectance = values.astype("float32") * metadata.get("scale", 1) + metadata.get("offset", 0)
            valid = reflectance.compressed()
            if valid.size == 0:
                raise ValueError(f"No hay píxeles válidos en {band}.")
            low, high = np.percentile(valid, [2, 98])
            channel = np.clip((reflectance.filled(low) - low) / max(float(high - low), 1e-6), 0, 1)
            channels.append(channel)
            masks.append(np.ma.getmaskarray(values))
        rgb = np.dstack(channels)
        rgb[np.logical_or.reduce(masks)] = 0
        preview_path = config.output_dir / first.id / "rgb_preview.png"
        mpimg.imsave(preview_path, rgb)
        title = f"{_item_tile(first)} | {_item_datetime(first)} | nubosidad {first.properties['eo:cloud_cover']:.1f}%"
        return RGBPreview(image=rgb, path=preview_path, title=title)
    else:
        print("La vista RGB requiere las bandas descargadas red, green y blue.")
        return None


def _leaflet_bounds(bounds) -> list[list[float]]:
    """Convierte la extensión del ráster a pares de latitud y longitud de Leaflet."""
    west, south, east, north = bounds
    return [[south, west], [north, east]]


def _crear_vista_mapa(config: PipelineConfig, scene_id: str, records: dict, *, resampling=Resampling.bilinear) -> dict:
    """Lee una vista reducida local o por rangos HTTP; no guarda bandas completas."""
    carpeta = config.output_dir / scene_id
    overlay_png = carpeta / "rgb_map_webmercator.png"
    cache_path = carpeta / "rgb_map_webmercator.json"
    firma = {"max_pixels": config.map_max_pixels, "channels": list(config.map_bands), "resampling": resampling.name,
             "min": config.map_min, "max": config.map_max, "bands": {
        banda: {clave: records[banda].get(clave) for clave in ["path", "source_url", "raster_bands", "band_index"]}
        for banda in config.map_bands}}
    firma["local_versions"] = {}
    for banda in config.map_bands:
        if records[banda].get("path"):
            local = config.output_dir / records[banda]["path"]
            if local.is_file():
                firma["local_versions"][banda] = [local.stat().st_mtime_ns, local.stat().st_size]
    if overlay_png.is_file() and cache_path.is_file():
        cache = json.loads(cache_path.read_text())
        if cache.get("signature") == firma:
            return cache["preview"]

    def fuente(record):
        if record.get("path"):
            ruta = config.output_dir / record["path"]
            if ruta.is_file():
                return str(ruta)
        if config.map_preview_remote:
            return record["source_url"]
        raise rasterio.errors.RasterioIOError("La banda no está descargada y la vista remota está desactivada.")

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT=60):
        reference_path = fuente(records[config.map_bands[0]])
        with rasterio.open(reference_path) as reference:
            projected_bounds = transform_bounds(reference.crs, "EPSG:3857", *reference.bounds,
                                                densify_pts=21)
        west, south, east, north = projected_bounds
        scale = config.map_max_pixels / max(east - west, north - south)
        map_width = max(1, int(np.ceil((east - west) * scale)))
        map_height = max(1, int(np.ceil((north - south) * scale)))
        map_transform = from_bounds(*projected_bounds, map_width, map_height)
        channels, valid_masks = [], []
        for band in config.map_bands:
            record = records[band]
            with ExitStack() as pila:
                ruta = fuente(record)
                base = pila.enter_context(rasterio.open(ruta))
                factor = max(base.width / map_width, base.height / map_height)
                niveles = [i for i, reduccion in enumerate(base.overviews(1)) if reduccion <= factor]
                # Fuerza una pirámide reducida antes de reproyectar para evitar leer la banda nativa completa.
                source = (pila.enter_context(rasterio.open(ruta, overview_level=niveles[-1]))
                          if niveles else base)
                with WarpedVRT(source, crs="EPSG:3857", transform=map_transform,
                               width=map_width, height=map_height,
                               resampling=resampling) as warped:
                    values = warped.read(record.get("band_index", 1), masked=True).astype("float32")
            metadata = (record.get("raster_bands") or [{}])[0]
            reflectance = values * metadata.get("scale", 1) + metadata.get("offset", 0)
            valid_values = reflectance.compressed()
            if not valid_values.size:
                raise ValueError(f"No hay píxeles válidos en {scene_id}/{band}.")
            low, high = ((config.map_min, config.map_max) if config.map_min is not None
                         else np.percentile(valid_values, [2, 98]))
            channels.append(np.clip((reflectance.filled(low) - low) / max(float(high - low), 1e-6), 0, 1))
            valid_masks.append(~np.ma.getmaskarray(values))
        alpha = np.logical_and.reduce(valid_masks).astype("float32")
        rgba = np.round(np.dstack(channels + [alpha]) * 255).astype("uint8")
        carpeta.mkdir(parents=True, exist_ok=True)
        mpimg.imsave(overlay_png, rgba)
        image_bounds = _leaflet_bounds(transform_bounds("EPSG:3857", "EPSG:4326", *projected_bounds))

    vista = {"scene_id": scene_id, "bounds": image_bounds,
             "shape": [map_height, map_width], "png": str(overlay_png)}
    cache_path.write_text(json.dumps({"signature": firma, "preview": vista}))
    return vista


def create_interactive_map(config: PipelineConfig, available: SearchResult | None = None) -> MapResult:
    """Exporta un mapa Folium con regiones, coberturas y vistas RGB locales o remotas."""
    if config.regions_zip is None or not config.regions_zip.is_file():
        raise FileNotFoundError(f"No se encontró el archivo de regiones: {config.regions_zip}")
    if not isinstance(config.map_max_pixels, int) or config.map_max_pixels < 128:
        raise ValueError("config.map_max_pixels debe ser un entero mayor o igual que 128.")
    if config.map_simplify_meters < 0:
        raise ValueError("config.map_simplify_meters debe ser mayor o igual que 0.")
    if (config.map_min is None) != (config.map_max is None) or (config.map_min is not None and config.map_max <= config.map_min):
        raise ValueError("Indique un mínimo y máximo de visualización válidos.")

    # GDAL lee el shapefile dentro del ZIP; no es necesario extraerlo.
    regions = load_regions(config)
    if regions.crs is None:
        raise ValueError("El shapefile de regiones debe incluir un sistema de referencia (.prj).")
    if regions.empty or regions.geometry.is_empty.any() or regions.geometry.isna().any():
        raise ValueError("El shapefile de regiones contiene geometrías vacías o ausentes.")
    if not regions.geometry.is_valid.all():
        regions.geometry = regions.geometry.make_valid()
    regions = regions.to_crs("EPSG:4326")
    if config.region_fields:
        region_fields = list(config.region_fields)
        missing = set(region_fields) - set(regions.columns)
        if missing:
            raise ValueError(f"No se encontraron estos campos de regiones: {sorted(missing)}")
    else:
        region_fields = [
            field for field in ["nombre", "macroregio", "regimen_fu", "prioridad", "LEVEL_1", "LEVEL_2", "LEVEL_3"]
            if field in regions
        ]
    if not region_fields:
        raise ValueError("Indique en config.region_fields los atributos de las regiones que desea mostrar.")
    color_field = config.region_color_field
    if color_field is None:
        color_field = next((field for field in ["macroregio", "LEVEL_2"] if field in regions), region_fields[0])
    if color_field not in regions:
        raise ValueError(f"No se encontró el campo usado para colorear las regiones: {color_field}")

    tile_path = config.output_dir / ("region_boundary.geojson" if config.region_id is not None else "tile_boundary.geojson")
    if not tile_path.is_file():
        raise FileNotFoundError("Ejecute primero la celda de selección del área de trabajo.")
    map_tile = gpd.read_file(tile_path).to_crs(4326)
    tile_geometry = map_tile.geometry.union_all()
    etiqueta_area = (str(map_tile.iloc[0][config.region_name_field]) if config.region_id is not None
                     else f"Tesela {config.tile_name}")
    intersecting_regions = regions.loc[regions.intersects(tile_geometry)]
    print(f"Se cargaron {len(regions)} regiones; intersecciones con {etiqueta_area}: {len(intersecting_regions)}.")

    # Simplifica en metros la copia de visualización para reducir el tamaño del HTML.
    display_fields = list(dict.fromkeys(region_fields + [color_field, "geometry"]))
    display_regions = regions[display_fields].to_crs(3857)
    if config.map_simplify_meters:
        display_regions.geometry = display_regions.geometry.simplify(
            config.map_simplify_meters, preserve_topology=True
        )
    display_regions = display_regions.to_crs(4326)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    display_regions.to_file(config.output_dir / "regions_display.geojson", driver="GeoJSON")

    manifest_path = config.output_dir / "download_manifest.json"
    map_manifest = json.loads(manifest_path.read_text()) if config.map_include_rgb and manifest_path.is_file() else []
    scene_records = {}
    for record in map_manifest:
        if not config.map_scene_ids or record["item_id"] in config.map_scene_ids:
            scene_records.setdefault(record["item_id"], {})[record["asset"]] = record
    ids_disponibles = {item.id for item in available.selected} if available is not None else set()
    missing_ids = set(config.map_scene_ids) - (set(scene_records) | ids_disponibles)
    if missing_ids:
        raise ValueError(f"Estas escenas no aparecen en la consulta ni en las descargas: {sorted(missing_ids)}")

    if available is not None:
        ids_seleccionados = {item.id for item in available.selected}
        scene_records = {key: value for key, value in scene_records.items() if key in ids_seleccionados}
        if config.map_include_rgb and config.map_preview_remote:
            for item in available.selected:
                if config.map_scene_ids and item.id not in config.map_scene_ids:
                    continue
                if all(b in item.assets for b in config.map_bands):
                    remotos = {
                        banda: {"source_url": item.assets[banda].href,
                                "raster_bands": item.assets[banda].extra_fields.get("raster:bands", []),
                                "datetime": _item_datetime(item),
                                "cloud_cover": item.properties["eo:cloud_cover"]}
                        for banda in config.map_bands
                    }
                    existentes = scene_records.setdefault(item.id, {})
                    for banda, record in remotos.items():
                        existentes.setdefault(banda, record)

    tile_bounds = _leaflet_bounds(map_tile.total_bounds)
    region_bounds = _leaflet_bounds(regions.total_bounds)
    center = [(tile_bounds[0][0] + tile_bounds[1][0]) / 2,
              (tile_bounds[0][1] + tile_bounds[1][1]) / 2]
    interactive_map = folium.Map(location=center, tiles=None, zoom_start=9,
                                 control_scale=True, prefer_canvas=True, height="100%")
    folium.map.CustomPane("scene-rasters", z_index=350, pointer_events=False).add_to(interactive_map)
    folium.map.CustomPane("monitor-vectors", z_index=600, pointer_events=True).add_to(interactive_map)
    folium.TileLayer(
        "Esri.WorldImagery", name="Mapa base: satélite (Esri)", show=True,
        max_zoom=20, max_native_zoom=19,
    ).add_to(interactive_map)
    folium.TileLayer(
        "Esri.WorldGrayCanvas", name="Mapa base: gris (Esri)", show=False,
    ).add_to(interactive_map)

    map_overlays = []
    orden_vistas = list(scene_records.items())
    if config.map_show_all:
        # Las escenas mejor clasificadas quedan encima de las demás.
        orden_vistas.reverse()
    for scene_id, records in tqdm(orden_vistas, desc="Vistas RGB", unit="escena", disable=len(orden_vistas) <= 1):
        if not all(band in records for band in config.map_bands):
            print(f"Se omite {scene_id}: faltan bandas de visualización.")
            continue
        try:
            vista = _crear_vista_mapa(config, scene_id, records)
        except (rasterio.errors.RasterioIOError, ValueError) as error:
            print(f"No se generó la vista RGB de {scene_id}: {error}")
            continue
        image_bounds = vista["bounds"]
        overlay_png = vista["png"]
        scene_date = records[config.map_bands[0]]["datetime"][:10]
        scene_cloud = records[config.map_bands[0]]["cloud_cover"]
        folium.raster_layers.ImageOverlay(
            image=str(overlay_png), bounds=image_bounds,
            name=f"Sentinel-2 {scene_date} | nubosidad {scene_cloud:.1f}% | {scene_id}",
            opacity=1, pixelated=False, show=config.map_show_all or not map_overlays,
            pane="scene-rasters",
            attribution="Copernicus Sentinel-2 / Element 84 Earth Search",
        ).add_to(interactive_map)
        map_overlays.append(vista)

    if available is not None and available.items:
        ids_visibles = set(available.scenes["id"])
        escenas_visibles = [item for item in available.items if item.id in ids_visibles]
        if escenas_visibles:
            geometrias = gpd.GeoSeries([shape(item.geometry) for item in escenas_visibles], crs=4326)
            # Une huellas repetidas entre fechas y limita la cobertura a la región.
            cobertura = gpd.GeoDataFrame(geometry=[geometrias.drop_duplicates().union_all().intersection(tile_geometry)], crs=4326)
            cobertura.to_file(config.output_dir / "available_coverage.geojson", driver="GeoJSON")
            cobertura_vista = cobertura.to_crs(3857)
            if config.map_simplify_meters:
                cobertura_vista.geometry = cobertura_vista.geometry.simplify(config.map_simplify_meters, preserve_topology=True)
            folium.GeoJson(
                json.loads(cobertura_vista.to_crs(4326).to_json()), name="Cobertura conjunta disponible", show=not config.map_include_rgb,
                pane="monitor-vectors",
                style_function=lambda feature: {"color": "#00a86b", "weight": 2, "fillOpacity": 0.22},
                tooltip="Cobertura geográfica de las escenas; puede contener nubes.",
            ).add_to(interactive_map)
        huellas = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": item.geometry, "properties": {
                "escena": item.id, "fecha": _item_datetime(item),
                "nubosidad": item.properties["eo:cloud_cover"], "tesela": _item_tile(item)}}
            for item in escenas_visibles
        ]}
        folium.GeoJson(
            huellas, name=f"Escenas disponibles ({len(huellas['features'])})", show=False,
            pane="monitor-vectors",
            style_function=lambda feature: {"color": "#00a6ff", "weight": 1, "fillOpacity": 0.025},
            tooltip=folium.GeoJsonTooltip(fields=["escena", "fecha", "nubosidad", "tesela"],
                                         aliases=["Escena", "Fecha UTC", "Nubosidad (%)", "Tesela"]),
        ).add_to(interactive_map)
    ruta_teselas = config.output_dir / "tiles_intersecting.geojson"
    if config.region_id is not None and ruta_teselas.is_file():
        teselas = gpd.read_file(ruta_teselas)
        if len(teselas):
            folium.GeoJson(
                json.loads(teselas.to_json()), name=f"Teselas de la región ({len(teselas)})", show=False,
                pane="monitor-vectors",
                style_function=lambda feature: {"color": "#555555", "weight": 1, "fillOpacity": 0},
                tooltip=folium.GeoJsonTooltip(fields=[config.tile_field], aliases=["Tesela"]),
            ).add_to(interactive_map)

    # Usa colores por macrorregión y conserva todos los polígonos de monitoreo.
    palette = ["#ff9800", "#e91e63", "#00bcd4", "#9c27b0", "#ffeb3b", "#4caf50", "#2196f3", "#795548"]
    display_regions[color_field] = display_regions[color_field].fillna("Sin especificar").astype(str)
    categories = sorted(display_regions[color_field].unique())
    region_colors = {category: palette[i % len(palette)] for i, category in enumerate(categories)}
    display_regions["_color"] = display_regions[color_field].map(region_colors).fillna("#ff9800")
    folium.GeoJson(
        json.loads(display_regions.to_json()),
        pane="monitor-vectors",
        name=f"Regiones de monitoreo del fuego ({len(regions)})",
        style_function=lambda feature: {"color": feature["properties"]["_color"],
                                        "weight": 2, "fillOpacity": 0.035},
        highlight_function=lambda feature: {"color": "#ff1744", "weight": 4, "fillOpacity": 0.12},
        tooltip=folium.GeoJsonTooltip(
            fields=region_fields, aliases=[_ETIQUETAS_ES.get(campo, campo) for campo in region_fields], sticky=False
        ),
        popup=folium.GeoJsonPopup(
            fields=region_fields, aliases=[_ETIQUETAS_ES.get(campo, campo) for campo in region_fields]
        ),
        zoom_on_click=True,
    ).add_to(interactive_map)
    display_area = map_tile.to_crs(3857)
    if config.map_simplify_meters:
        display_area.geometry = display_area.geometry.simplify(config.map_simplify_meters, preserve_topology=True)
    folium.GeoJson(
        json.loads(display_area.to_crs(4326).to_json()), name=f"Área seleccionada: {etiqueta_area}",
        pane="monitor-vectors",
        style_function=lambda feature: {"color": "#ff5722", "weight": 3,
                                        "fillOpacity": 0, "dashArray": "8 5"},
        tooltip=etiqueta_area,
    ).add_to(interactive_map)
    Fullscreen(title="Pantalla completa", title_cancel="Salir de pantalla completa").add_to(interactive_map)
    MousePosition(
        position="bottomright", empty_string="Sin coordenadas", prefix="Latitud, longitud:"
    ).add_to(interactive_map)
    folium.LayerControl(collapsed=False).add_to(interactive_map)
    # Traduce los títulos y las etiquetas de accesibilidad de los controles de Leaflet.
    controles = MacroElement()
    controles._template = Template("""
    {% macro script(this, kwargs) %}
    var contenedor = {{ this._parent.get_name() }}.getContainer();
    contenedor.setAttribute('lang', 'es');
    document.documentElement.setAttribute('lang', 'es');
    [['.leaflet-control-zoom-in', 'Acercar'],
     ['.leaflet-control-zoom-out', 'Alejar'],
     ['.leaflet-control-layers-toggle', 'Capas']].forEach(function(par) {
        var control = contenedor.querySelector(par[0]);
        if (control) {
            control.setAttribute('title', par[1]);
            control.setAttribute('aria-label', par[1]);
        }
    });
    {% endmacro %}
    """)
    controles.add_to(interactive_map)
    interactive_map.fit_bounds(tile_bounds)

    legend = "".join(
        f'<div><span style="color:{color};font-weight:bold">━</span> {escape(str(category))}</div>'
        for category, color in region_colors.items()
    )
    map_name = interactive_map.get_name()
    panel = f"""
    <div style="position:fixed;bottom:30px;left:12px;z-index:1000;background:white;
                color:#222;padding:12px;border-radius:6px;box-shadow:0 1px 6px #777;
                font:13px Arial;max-width:250px">
      <b>Sentinel-2 · {escape(etiqueta_area)}</b><br>
      <button type="button" style="margin:6px 2px 6px 0"
        onclick='{map_name}.fitBounds({json.dumps(tile_bounds)})'>Ver área seleccionada</button>
      <button type="button" onclick='{map_name}.fitBounds({json.dumps(region_bounds)})'>Todas las regiones</button>
      <div style="font-weight:bold;margin-bottom:4px">Regiones de monitoreo del fuego</div>{legend}
      <div style="margin-top:6px">Pase el cursor para ver el nombre · Pulse una región para acercar</div>
    </div>"""
    interactive_map.get_root().html.add_child(folium.Element(panel))
    interactive_map.get_root().header.add_child(folium.Element(
        f"<title>Monitoreo del fuego — Sentinel-2 {escape(etiqueta_area)}</title>"
    ))
    interactive_map.get_root().header.add_child(folium.Element(
        "<style>.leaflet-control-layers-list {max-height:320px;overflow-y:auto;max-width:420px;}</style>"
    ))

    map_path = config.output_dir / "interactive_map.html"
    interactive_map.save(str(map_path))
    (config.output_dir / "map_layers.json").write_text(json.dumps({
        "tile": config.tile_name if config.region_id is None else None,
        "region_id": config.region_id, "selected_area": etiqueta_area, "regions_source": str(config.regions_zip), "region_count": len(regions),
        "regions_layer": config.regions_layer, "region_fields": region_fields, "region_color_field": color_field,
        "regions_intersecting_tile": intersecting_regions[region_fields].to_dict("records"),
        "image_crs": "EPSG:3857", "imagery": map_overlays,
        "include_rgb": config.map_include_rgb,
    }, indent=2))
    print(f"Mapa interactivo: {map_path} ({map_path.stat().st_size / 1e6:.1f} MB)")
    if config.map_include_rgb:
        print(f"Se añadieron {len(map_overlays)} capas RGB de Sentinel-2 y las {len(regions)} regiones de monitoreo del fuego.")
    else:
        print("Cobertura calculada con metadatos; no se leyeron bandas ni se generaron vistas RGB.")
    return MapResult(map=interactive_map, path=map_path,
                     intersecting_regions=intersecting_regions[region_fields].copy(),
                     overlays=map_overlays, region_count=len(regions))


def _masked_reflectance_source(item: Item, band: str, path: Path, scl_path: Path,
                               overlap, overlap_crs, destination: Path,
                               scl_exclude: tuple[int, ...], block_size: int) -> Path:
    """Enmascara reflectancia en su malla nativa antes de interpolarla.

    Trabaja por bloques y recorta al entorno de la región con dos píxeles de
    margen. No modifica las imágenes originales.
    """
    with rasterio.open(path) as src, rasterio.open(scl_path) as scl:
        if src.crs is None or scl.crs is None:
            raise ValueError(f"Falta el sistema de referencia de {item.id}/{band} o SCL.")
        metadata = (item.assets[band].extra_fields.get("raster:bands") or [{}])[0]
        if "scale" not in metadata and src.scales[0] == 1 and np.issubdtype(np.dtype(src.dtypes[0]), np.integer):
            raise ValueError(f"Falta la escala de reflectancia de {item.id}/{band}.")
        scale, offset = metadata.get("scale", src.scales[0]), metadata.get("offset", src.offsets[0])
        bounds = transform_bounds(overlap_crs, src.crs, *overlap.bounds, densify_pts=21)
        candidate = rasterio.windows.from_bounds(*bounds, transform=src.transform)
        left, top = max(0, int(np.floor(candidate.col_off)) - 2), max(0, int(np.floor(candidate.row_off)) - 2)
        right = min(src.width, int(np.ceil(candidate.col_off + candidate.width)) + 2)
        bottom = min(src.height, int(np.ceil(candidate.row_off + candidate.height)) + 2)
        if right <= left or bottom <= top:
            raise ValueError(f"La imagen {item.id}/{band} no cubre el área esperada.")
        crop = Window(left, top, right - left, bottom - top)
        native_transform = src.window_transform(crop)
        profile = dict(driver="GTiff", width=int(crop.width), height=int(crop.height), count=1,
                       crs=src.crs, transform=native_transform, dtype="float32", nodata=float("nan"),
                       tiled=True, blockxsize=block_size, blockysize=block_size,
                       compress="DEFLATE", predictor=3, BIGTIFF="IF_SAFER")
        with WarpedVRT(scl, crs=src.crs, transform=native_transform, width=int(crop.width), height=int(crop.height),
                       resampling=Resampling.nearest, src_nodata=scl.nodata if scl.nodata is not None else 0,
                       nodata=0, warp_mem_limit=32) as native_scl, rasterio.open(destination, "w", **profile) as output:
            for _, window in output.block_windows(1):
                source_window = Window(crop.col_off + window.col_off, crop.row_off + window.row_off,
                                       window.width, window.height)
                values = src.read(1, window=source_window, masked=True).astype("float32").filled(np.nan)
                reflectance = values * scale + offset
                classes = native_scl.read(1, window=window, masked=True)
                valid = (~np.ma.getmaskarray(classes) & ~np.isin(classes.filled(0), scl_exclude)
                         & np.isfinite(reflectance) & (reflectance > 0))
                output.write(np.where(valid, reflectance, np.nan).astype("float32"), 1, window=window)
    return destination


_SCENE_PHASES = ("mask_s", "resample_s", "indices_s", "mosaic_s", "cleanup_s")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def _time_phase(durations: dict, phase: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        durations[phase] += time.perf_counter() - start


class _SceneTimingLog:
    """Escribe un CSV atómico; la descarga y el proceso actualizan filas distintas."""
    def __init__(self, path: Path, items: list[Item]):
        self.path = path
        self.lock = Lock()
        self.rows = {item.id: {
            "item_id": item.id, "tile": _item_tile(item), "datetime": _item_datetime(item), "status": "pendiente",
            "cloud_cover": item.properties.get("eo:cloud_cover"),
            "download_s": 0.0, "wait_s": 0.0, **{key: 0.0 for key in _SCENE_PHASES},
            "process_s": 0.0, "scene_elapsed_s": 0.0,
            "download_started_utc": "", "download_finished_utc": "",
            "processing_started_utc": "", "processing_finished_utc": "", "error": "",
        } for item in items}
        self._save()

    def _save(self):
        temporary = self.path.with_suffix(".tmp.csv")
        format_table(pd.DataFrame(list(self.rows.values()))).to_csv(temporary, index=False, float_format="%.6f")
        temporary.replace(self.path)

    def update(self, item_id: str, **values):
        with self.lock:
            self.rows[item_id].update(values)
            self._save()

    def frame(self) -> pd.DataFrame:
        with self.lock:
            return pd.DataFrame([row.copy() for row in self.rows.values()])


def build_fire_composite(
    config: PipelineConfig, items: list[Item], region: gpd.GeoDataFrame, *,
    resolution_m: float = 20, crs: str | None = None, block_size: int = 512,
    keep_inputs: bool = False, input_paths: dict[str, dict[str, Path]] | None = None,
    scl_exclude: tuple[int, ...] = (0, 1, 2, 3, 8, 9, 10, 11),
) -> FireCompositeResult:
    """Descarga por escena y compone por bloques usando el máximo NBR invertido.

    input_paths permite reutilizar archivos locales sin hacer solicitudes HTTP.
    Solo se eliminan carpetas temporales creadas por esta llamada, si keep_inputs
    es False. Los archivos suministrados por input_paths nunca se eliminan.
    """
    run_started = time.perf_counter()
    if not items or region.empty or region.crs is None:
        raise ValueError("Seleccione escenas y una región con sistema de referencia.")
    if not np.isfinite(resolution_m) or resolution_m <= 0 or block_size < 16 or block_size % 16:
        raise ValueError("Use resolución positiva en metros y bloques múltiplos de 16.")
    if input_paths is None and not config.download_images:
        raise ValueError("Active la descarga para preparar el mosaico o suministre input_paths.")
    spectral_options = {_PC_BANDS[b] for b in _PC_BANDS if b.startswith("B")}
    spectral_assets = tuple(dict.fromkeys((*FIRE_REQUIRED_ASSETS[:-1],
                                          *(b for b in config.bands if b in spectral_options))))
    extra_assets = tuple(b for b in spectral_assets if b not in FIRE_REQUIRED_ASSETS)
    composite_bands = (*FIRE_COMPOSITE_BANDS, *extra_assets)
    input_assets = (*spectral_assets, "scl")
    # Valida todas las escenas antes de descargar o crear resultados.
    ordered = sorted({item.id: item for item in items}.values(), key=lambda item: (_item_datetime(item), item.id))
    for item in ordered:
        _require_l2a(item)
        faltantes = set(input_assets) - item.assets.keys()
        if faltantes:
            raise ValueError(f"Faltan bandas para NBR y máscara SCL en {item.id}: {sorted(faltantes)}")
        if input_paths is not None:
            for band in input_assets:
                if not Path(input_paths.get(item.id, {}).get(band, "")).is_file():
                    raise FileNotFoundError(f"Falta el archivo local {item.id}/{band}.")

    target_crs = rasterio.crs.CRS.from_user_input(crs or region.estimate_utm_crs())
    if not target_crs.is_projected or target_crs.linear_units != "metre":
        raise ValueError("El mosaico necesita un sistema de referencia proyectado en metros.")
    area = region.to_crs(target_crs).geometry.union_all()
    west, south, east, north = area.bounds
    west, south = np.floor(west / resolution_m) * resolution_m, np.floor(south / resolution_m) * resolution_m
    east, north = np.ceil(east / resolution_m) * resolution_m, np.ceil(north / resolution_m) * resolution_m
    width, height = int(round((east - west) / resolution_m)), int(round((north - south) / resolution_m))
    transform = from_origin(west, north, resolution_m, resolution_m)
    folder = config.output_dir / f"fire_composite_{config.start_date}_{config.end_date}"
    folder.mkdir(parents=True, exist_ok=True)
    # Un archivo de trabajo sin compresión evita fragmentación al actualizar bloques.
    estimated_bytes = width * height * len(composite_bands) * 4
    mask_workspace_bytes = 0
    for item in ordered:
        scene_bytes = 0
        for band in spectral_assets:
            if input_paths is not None:
                with rasterio.open(input_paths[item.id][band]) as src:
                    scene_bytes += src.width * src.height * 4
            else:
                scene_bytes += int(np.prod(item.assets[band].extra_fields.get("proj:shape", [10980, 10980]))) * 4
        mask_workspace_bytes = max(mask_workspace_bytes, scene_bytes)
    free_bytes = shutil.disk_usage(folder).free
    fixed_reserve = mask_workspace_bytes + 256 * 1024**2
    required_bytes = int(estimated_bytes * 2.5 + fixed_reserve)
    storage_plan = {
        "resolucion_m": resolution_m, "ancho_pixeles": width, "alto_pixeles": height,
        "bandas": len(composite_bands), "trabajo_sin_comprimir_bytes": estimated_bytes,
        "reserva_requerida_bytes": required_bytes, "espacio_libre_bytes": free_bytes,
        "nota": "Estimación conservadora para trabajo, COG y máscara; las descargas requieren espacio adicional.",
    }
    suggestion = "Libere espacio o procese una región más pequeña."
    if free_bytes > fixed_reserve:
        minimum_resolution = resolution_m * np.sqrt(estimated_bytes * 2.5 / (free_bytes - fixed_reserve))
        recommended_resolution = max(resolution_m, float(np.ceil(minimum_resolution / 10) * 10))
        storage_plan["resolucion_estimada_m"] = recommended_resolution
        suggestion = (f"Pruebe --resolution {recommended_resolution:g} "
                      f"(RESOLUCION_MOSAICO = {recommended_resolution:g} en el cuaderno), "
                      "dejando espacio adicional para las descargas. Para mantener la resolución, "
                      "necesita más disco o dividir el área de procesamiento.")
    storage_path = folder / "storage_plan.json"
    storage_path.write_text(json.dumps(storage_plan, indent=2, ensure_ascii=False))
    print(f"Cuadrícula: {width} × {height} píxeles a {resolution_m:g} m; "
          f"trabajo sin comprimir: {estimated_bytes / 1e9:.1f} GB; "
          f"reserva: {required_bytes / 1e9:.1f} GB; libres: {free_bytes / 1e9:.1f} GB.")
    if required_bytes > free_bytes:
        raise ValueError(
            f"No hay espacio libre para el mosaico a {resolution_m:g} m: "
            f"se requieren aproximadamente {required_bytes / 1e9:.1f} GB y hay {free_bytes / 1e9:.1f} GB libres. "
            f"{suggestion} Reducir las fechas no reduce la cuadrícula de la región. "
            f"Detalle: {storage_path}"
        )
    work_path = folder / "fire_quality_mosaic.work.tif"
    final_path = folder / "fire_quality_mosaic.tif"
    partial_path = folder / "fire_quality_mosaic.partial.tif"
    metadata_path = folder / "fire_quality_mosaic.json"
    metadata = {
        "status": "in_progress", "region_id": config.region_id,
        "start_date": config.start_date, "end_date": config.end_date,
        "crs": str(target_crs), "resolution_m": resolution_m, "shape": [height, width],
        "bands": list(composite_bands), "spectral_bands": ["red", "nir", "swir1", "swir2", *extra_assets], "NBR": "(nir - swir2) / (nir + swir2)",
        "source": config.source, "stac_url": config.stac_url, "collection": config.collection,
        "workflow_parameters": config.workflow_parameters,
        "NDVI": "(nir - red) / (nir + red)", "quality": "int16(-1000 * (NBR + 1))",
        "selection": "Máximo de calidad por píxel; todas las bandas proceden de la misma observación.",
        "tie_break": "En empate de calidad entera: fecha más reciente; después identificador mayor.",
        "scl_excluded": list(scl_exclude), "spectral_resampling": "bilinear", "scl_resampling": "nearest",
        "mask_before_interpolation": True,
        "grid_transform": list(transform)[:6],
        "reflectance": "Escala y desplazamiento de los metadatos STAC; solo valores positivos.",
        "time_units": "Días desde 1970-01-01 (UTC)", "keep_inputs": keep_inputs,
        "scenes": [{"scene_index": index, "id": item.id, "datetime": _item_datetime(item),
                    "tile": _item_tile(item), "cloud_cover": item.properties.get("eo:cloud_cover")}
                   for index, item in enumerate(ordered, 1)],
    }
    scene_pixel_counts = {index: 0 for index in range(1, len(ordered) + 1)}
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    profile = dict(driver="GTiff", width=width, height=height, count=len(composite_bands),
                   crs=target_crs, transform=transform, dtype="float32", nodata=float("nan"),
                   tiled=True, blockxsize=block_size, blockysize=block_size, BIGTIFF="IF_SAFER", SPARSE_OK=True)
    timings_path = folder / "scene_timings.csv"
    timing_summary_path = folder / "process_timings.json"
    log = _SceneTimingLog(timings_path, ordered)
    summary = {"estado": "en_curso", "preparacion_s": 0.0, "escenas_s": 0.0,
               "finalizacion_cog_s": 0.0, "total_s": 0.0, "escenas_anticipadas": 1 if input_paths is None else 0,
               "nota": "Descarga y procesamiento se solapan; sus sumas no equivalen al tiempo total."}
    metadata.update(timings_csv=str(timings_path), timing_summary_json=str(timing_summary_path))
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    stop_downloads = Event()
    owned_inputs = []
    progress_lock = Lock()
    download_progress = {}
    download_starts = {}
    valid_pixels = 0
    active_jobs = []
    loop_started = None

    def save_summary():
        temporary = timing_summary_path.with_suffix(".tmp.json")
        temporary.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        temporary.replace(timing_summary_path)

    def clean_pending_inputs():
        # ExitStack espera a los hilos antes de ejecutar esta limpieza.
        if not keep_inputs:
            for directory in owned_inputs:
                if directory.is_dir():
                    shutil.rmtree(directory)

    def download_scene(job):
        _, item, _ = job
        started = time.perf_counter()
        download_starts[item.id] = started
        log.update(item.id, status="descargando", download_started_utc=_utc_timestamp())
        try:
            if stop_downloads.is_set():
                raise CancelledError("Descarga anticipada cancelada.")
            scratch = Path(tempfile.mkdtemp(prefix="scene_inputs_", dir=folder))
            owned_inputs.append(scratch)
            scene_config = replace(config, data_dir=scratch, download_images=True,
                                   bands=tuple(dict.fromkeys((*config.bands, *FIRE_REQUIRED_ASSETS))))
            allocated = work_path.stat().st_blocks * 512
            reserved = max(estimated_bytes - allocated, 0) + int(estimated_bytes * 1.5) + mask_workspace_bytes
            def band_progress(done, total):
                with progress_lock:
                    download_progress[item.id] = (done, total)
            downloaded = download_scenes(scene_config, [item], reserve_bytes=reserved, quiet=True,
                                         on_progress=band_progress, cancel_event=stop_downloads)
            finished = time.perf_counter()
            log.update(item.id, status="descargada", download_s=finished - started,
                       download_finished_utc=_utc_timestamp())
            return {"paths": downloaded.paths[item.id], "scratch": scratch,
                    "started": started, "finished": finished}
        except BaseException as error:
            elapsed = time.perf_counter() - started
            log.update(item.id, status="error_descarga", download_s=elapsed, scene_elapsed_s=elapsed,
                       download_finished_utc=_utc_timestamp(), error=f"{type(error).__name__}: {error}")
            raise

    save_summary()
    try:
        for scene_index, item in enumerate(ordered, 1):
            footprint = gpd.GeoSeries([shape(item.geometry)], crs=4326).to_crs(target_crs).iloc[0]
            overlap = footprint.intersection(area)
            if overlap.is_empty:
                log.update(item.id, status="sin_interseccion")
            else:
                active_jobs.append((scene_index, item, overlap))
        with ExitStack() as lifetime:
            lifetime.callback(clean_pending_inputs)
            executor = lifetime.enter_context(ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentinel-prefetch")) if input_paths is None and active_jobs else None
            future = None
            try:
                with rasterio.Env(GDAL_CACHEMAX=64 * 1024**2), rasterio.open(work_path, "w+", **profile) as output, \
                        tqdm(active_jobs, desc="Escenas procesadas", unit="escena", mininterval=1) as scene_progress:
                    output.descriptions = composite_bands
                    output.update_tags(quality=metadata["quality"], region_id=str(config.region_id))
                    loop_started = time.perf_counter()
                    summary["preparacion_s"] = loop_started - run_started
                    save_summary()
                    if executor is not None:
                        future = executor.submit(download_scene, active_jobs[0])
                    for position, (scene_index, item, overlap) in enumerate(scene_progress):
                        scratch = None
                        masked_scratch = None
                        processing_started = None
                        scene_started = time.perf_counter()
                        durations = {key: 0.0 for key in _SCENE_PHASES}
                        success = False
                        wait_started = time.perf_counter()
                        try:
                            if future is not None:
                                # Solo el hilo principal actualiza la barra de Jupyter.
                                while not future.done():
                                    wait([future], timeout=1)
                                    with progress_lock:
                                        done, total = download_progress.get(item.id, (0, len(config.bands)))
                                    scene_progress.set_postfix_str(f"Descarga {done}/{total} bandas", refresh=True)
                                try:
                                    ready = future.result()
                                finally:
                                    log.update(item.id, wait_s=time.perf_counter() - wait_started)
                                paths, scratch = ready["paths"], ready["scratch"]
                                scene_started = ready["started"]
                                if keep_inputs:
                                    metadata["scenes"][scene_index - 1]["inputs_dir"] = str(next(iter(paths.values())).parent)
                            else:
                                paths = input_paths[item.id]
                            processing_started = time.perf_counter()
                            log.update(item.id, status="procesando", processing_started_utc=_utc_timestamp())
                            metadata["scenes"][scene_index - 1]["reflectance_calibration"] = {
                                band: item.assets[band].extra_fields.get("raster:bands", [])
                                for band in spectral_assets
                            }
                            # No se encola una tercera escena: como máximo actual + siguiente.
                            future = (executor.submit(download_scene, active_jobs[position + 1])
                                      if executor is not None and position + 1 < len(active_jobs) else None)
                            scene_progress.set_postfix_str("Máscara · descarga anticipada" if future is not None else "Máscara y remuestreo", refresh=True)
                            with _time_phase(durations, "mask_s"):
                                masked_scratch = Path(tempfile.mkdtemp(prefix="masked_inputs_", dir=folder))
                                clean_paths = {
                                    band: _masked_reflectance_source(item, band, paths[band], paths["scl"], overlap,
                                                                     target_crs, masked_scratch / f"{band}.tif",
                                                                     scl_exclude, block_size)
                                    for band in spectral_assets
                                }
                            with ExitStack() as stack:
                                warped = {}
                                with _time_phase(durations, "resample_s"):
                                    for band in input_assets:
                                        src = stack.enter_context(rasterio.open(paths[band] if band == "scl" else clean_paths[band]))
                                        warped[band] = stack.enter_context(WarpedVRT(
                                            src, crs=target_crs, transform=transform, width=width, height=height,
                                            dtype="float32", nodata=0 if band == "scl" else float("nan"),
                                            resampling=Resampling.nearest if band == "scl" else Resampling.bilinear,
                                            warp_mem_limit=32,
                                        ))
                                candidate = rasterio.windows.from_bounds(*overlap.bounds, transform=transform)
                                col_start = max(0, int(np.floor(candidate.col_off / block_size)) * block_size)
                                row_start = max(0, int(np.floor(candidate.row_off / block_size)) * block_size)
                                col_stop = min(width, int(np.ceil(candidate.col_off + candidate.width)))
                                row_stop = min(height, int(np.ceil(candidate.row_off + candidate.height)))
                                acquisition = date.fromisoformat(_item_datetime(item)[:10])
                                scene_progress.set_postfix_str("Composición · descarga anticipada" if future is not None else "Composición NBR", refresh=True)
                                for row in range(row_start, row_stop, block_size):
                                    for col in range(col_start, col_stop, block_size):
                                        with _time_phase(durations, "mosaic_s"):
                                            window = Window(col, row, min(block_size, width - col), min(block_size, height - row))
                                            bounds = rasterio.windows.bounds(window, transform)
                                            polygon = area.intersection(box(*bounds))
                                            if polygon.is_empty:
                                                continue
                                            shape_window = (int(window.height), int(window.width))
                                            valid = geometry_mask([polygon.__geo_interface__], out_shape=shape_window,
                                                                  transform=rasterio.windows.transform(window, transform), invert=True)
                                        with _time_phase(durations, "resample_s"):
                                            scl = warped["scl"].read(1, window=window, masked=True)
                                            valid &= ~np.ma.getmaskarray(scl) & ~np.isin(scl.filled(0), scl_exclude)
                                            reflectance = {}
                                            for band in spectral_assets:
                                                values = warped[band].read(1, window=window, masked=True).filled(np.nan)
                                                reflectance[band] = values
                                                valid &= np.isfinite(values) & (values > 0)
                                        if not valid.any():
                                            continue
                                        with _time_phase(durations, "indices_s"):
                                            red, nir, swir1, swir2 = (reflectance[b] for b in FIRE_REQUIRED_ASSETS[:-1])
                                            nbr = np.full(shape_window, np.nan, dtype="float32")
                                            ndvi = np.full(shape_window, np.nan, dtype="float32")
                                            np.divide(nir - swir2, nir + swir2, out=nbr, where=valid)
                                            np.divide(nir - red, nir + red, out=ndvi, where=valid)
                                            quality = np.full(shape_window, np.nan, dtype="float32")
                                            quality[valid] = np.trunc(-1000 * (nbr[valid] + 1)).astype("int16")
                                        with _time_phase(durations, "mosaic_s"):
                                            current = output.read(window=window)
                                            previous_valid = np.isfinite(current[6])
                                            update = valid & (~previous_valid | (quality >= current[6]))
                                            if not update.any():
                                                continue
                                            valid_pixels += int(np.count_nonzero(update & ~previous_valid))
                                            replaced, counts = np.unique(current[9][update & previous_valid], return_counts=True)
                                            for old_index, count in zip(replaced, counts):
                                                scene_pixel_counts[int(old_index)] -= int(count)
                                            scene_pixel_counts[scene_index] += int(np.count_nonzero(update))
                                            for index, values in enumerate([red, nir, swir1, swir2, ndvi, nbr, quality]):
                                                current[index][update] = values[update]
                                            for index, band in enumerate(extra_assets, len(FIRE_COMPOSITE_BANDS)):
                                                current[index][update] = reflectance[band][update]
                                            current[7][update] = acquisition.month
                                            current[8][update] = (acquisition - date(1970, 1, 1)).days
                                            current[9][update] = scene_index
                                            output.write(current, window=window)
                            success = True
                            metadata["processed_scenes"] = metadata.get("processed_scenes", 0) + 1
                            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
                        except BaseException as error:
                            stop_downloads.set()
                            log.update(item.id, status="error_procesamiento" if processing_started is not None else "error_descarga",
                                       error=f"{type(error).__name__}: {error}")
                            raise
                        finally:
                            with _time_phase(durations, "cleanup_s"):
                                if masked_scratch is not None:
                                    shutil.rmtree(masked_scratch)
                                if scratch is not None and not keep_inputs:
                                    shutil.rmtree(scratch)
                            now = time.perf_counter()
                            elapsed_start = download_starts.get(item.id, scene_started)
                            update_values = dict(durations, process_s=now - processing_started if processing_started is not None else 0,
                                                 scene_elapsed_s=now - elapsed_start)
                            if processing_started is not None:
                                update_values["processing_finished_utc"] = _utc_timestamp()
                            if success:
                                update_values["status"] = "completada"
                            log.update(item.id, **update_values)
            except BaseException:
                stop_downloads.set()
                if future is not None:
                    future.cancel()
                raise
        summary["escenas_s"] = time.perf_counter() - loop_started
        if not valid_pixels:
            raise ValueError("No quedaron píxeles válidos tras aplicar SCL y validar reflectancias; revise escenas y fechas.")
        print("Finalizando GeoTIFF de la composición…")
        with _time_phase(summary, "finalizacion_cog_s"):
            if estimated_bytes * 1.5 + 256 * 1024**2 > shutil.disk_usage(folder).free:
                raise ValueError("No hay espacio libre para finalizar el COG; el archivo de trabajo se conserva.")
            copy_raster(work_path, partial_path, driver="COG", COMPRESS="DEFLATE", PREDICTOR="FLOATING_POINT",
                        BLOCKSIZE=512, BIGTIFF="IF_SAFER", OVERVIEW_RESAMPLING="NEAREST", NUM_THREADS="2")
            partial_path.replace(final_path)
            work_path.unlink()
        metadata.update(status="complete", valid_pixels=valid_pixels, path=str(final_path))
        for scene in metadata["scenes"]:
            scene["mosaic_pixels"] = scene_pixel_counts[scene["scene_index"]]
        metadata["images_used"] = [scene["id"] for scene in metadata["scenes"] if scene["mosaic_pixels"] > 0]
        summary["estado"] = "completado"
    except BaseException as error:
        if loop_started is not None and not summary["escenas_s"]:
            summary["escenas_s"] = time.perf_counter() - loop_started
        metadata.update(status="failed", error=f"{type(error).__name__}: {error}")
        summary.update(estado="error", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        frame = log.frame()
        summary["sumas_etapas_s"] = {key: float(frame[key].sum())
                                     for key in ("download_s", "wait_s", *_SCENE_PHASES, "process_s")}
        summary["total_s"] = time.perf_counter() - run_started
        save_summary()
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    print(f"Mosaico de fuego: {final_path}; píxeles válidos: {valid_pixels}.")
    print("Registro por escena:", timings_path)
    return FireCompositeResult(final_path, metadata_path, valid_pixels, len(active_jobs), timings_path, timing_summary_path)


@contextmanager
def _record_map_timing(result: FireCompositeResult):
    started = time.perf_counter()
    status = "error"
    try:
        yield
        status = "completado"
    finally:
        if result.timing_summary_path is not None:
            summary = json.loads(result.timing_summary_path.read_text())
            summary["mapa_s"] = time.perf_counter() - started
            summary["estado_mapa"] = status
            summary["total_con_mapa_s"] = summary["total_s"] + summary["mapa_s"]
            temporary = result.timing_summary_path.with_suffix(".tmp.json")
            temporary.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
            temporary.replace(result.timing_summary_path)


def create_fire_composite_map(config: PipelineConfig, result: FireCompositeResult,
                              available: SearchResult | None = None, *, max_pixels: int = 1536,
                              vis_params: dict | None = None) -> MapResult:
    """Muestra el mosaico SWIR1–NIR–rojo y su NBR, con vectores siempre encima."""
    with _record_map_timing(result):
        from matplotlib import colormaps
        from branca.colormap import LinearColormap

        base_config = replace(config, map_include_rgb=False, map_scene_ids=(), map_preview_remote=False)
        mapa = create_interactive_map(base_config, available=available)
        for key, child in list(mapa.map._children.items()):
            if isinstance(child, folium.LayerControl):
                del mapa.map._children[key]
            elif getattr(child, "layer_name", "") == "Cobertura conjunta disponible":
                child.show = False
        records = {band: {"path": str(result.path), "band_index": FIRE_COMPOSITE_BANDS.index(band) + 1,
                           "raster_bands": [{"scale": 1, "offset": 0}]} for band in ["swir1", "swir2", "nir", "red", "NBR"]}
        vis = vis_params or {"bands": ["swir1", "nir", "red"], "min": 0.03, "max": 0.4}
        preview_config = replace(config, map_bands=tuple(vis["bands"]), map_min=vis["min"], map_max=vis["max"],
                                 map_preview_remote=False, map_max_pixels=max_pixels)
        color = _crear_vista_mapa(preview_config, "quality_mosaic_color", records)
        labels = {"swir1": "SWIR1", "swir2": "SWIR2", "nir": "NIR", "red": "rojo"}
        folium.raster_layers.ImageOverlay(
            image=color["png"], bounds=color["bounds"], name="Mosaico de fuego · " + " / ".join(labels[b] for b in vis["bands"]),
            pane="scene-rasters", opacity=1, show=True, attribution="Copernicus Sentinel-2 L2A",
        ).add_to(mapa.map)
        nbr_config = replace(preview_config, map_bands=("NBR", "NBR", "NBR"), map_min=-1, map_max=1)
        nbr = _crear_vista_mapa(nbr_config, "quality_mosaic_nbr", records)
        gray = mpimg.imread(nbr["png"])
        rgba = colormaps["RdYlGn"](gray[:, :, 0])
        rgba[:, :, 3] = gray[:, :, 3]
        nbr_png = result.path.parent / "nbr_map_webmercator.png"
        mpimg.imsave(nbr_png, rgba)
        folium.raster_layers.ImageOverlay(
            image=str(nbr_png), bounds=nbr["bounds"], name="NBR del mosaico de fuego",
            pane="scene-rasters", opacity=1, show=False, attribution="NBR calculado con Sentinel-2 L2A",
        ).add_to(mapa.map)
        LinearColormap(["#a50026", "#ffffbf", "#006837"], vmin=-1, vmax=1,
                       caption="NBR del mosaico (bajo → alto)").add_to(mapa.map)
        folium.LayerControl(collapsed=False).add_to(mapa.map)
        mapa.path = result.path.parent / "quality_mosaic_map.html"
        mapa.map.save(str(mapa.path))
        mapa.overlays.extend([color, nbr])
        print("Mapa de la composición:", mapa.path)
        return mapa


def load_fire_composite(config: PipelineConfig) -> FireCompositeResult:
    """Abre un resultado terminado y comprueba los parámetros de la vista GEE."""
    folder = config.output_dir / f"fire_composite_{config.start_date}_{config.end_date}"
    path, metadata_path = folder / "fire_quality_mosaic.tif", folder / "fire_quality_mosaic.json"
    if not path.is_file() or not metadata_path.is_file():
        raise FileNotFoundError(f"No se encontró una composición guardada en {folder}.")
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("status") != "complete":
        raise ValueError("La composición guardada no terminó correctamente.")
    if metadata.get("source", "earth-search") != config.source:
        raise ValueError("El mosaico guardado corresponde a otro proveedor.")
    if config.workflow_parameters is not None:
        # Cambiar el contraste o los canales de visualización no modifica el COG.
        ignored = {"vis", "project", "tiles_asset"}
        saved = metadata.get("workflow_parameters")
        current_data = {k: v for k, v in config.workflow_parameters.items() if k not in ignored}
        saved_data = {k: v for k, v in (saved or {}).items() if k not in ignored}
        if current_data != saved_data:
            raise ValueError("El mosaico guardado no tiene los mismos parámetros de la vista GEE. Use la configuración original o genere la nueva composición.")
    timings, summary = folder / "scene_timings.csv", folder / "process_timings.json"
    return FireCompositeResult(path, metadata_path, metadata["valid_pixels"], metadata.get("processed_scenes", 0),
                               timings if timings.is_file() else None, summary if summary.is_file() else None)


def create_comparison_map(config: PipelineConfig, result: FireCompositeResult, gee_rgb_url: str,
                          *, gee_nbr_url: str | None = None, vis_params: dict | None = None,
                          max_pixels: int = 1536) -> tuple[DualMap, Path]:
    """Dos mapas sincronizados: teselas remotas GEE y vistas del COG local.

    No solicita escenas ni vuelve a calcular el mosaico. Las URL de GEE pueden
    caducar: al reabrir la comparación debe obtenerlas con getMapId de nuevo.
    """
    local = create_fire_composite_map(config, result, max_pixels=max_pixels, vis_params=vis_params)
    boundary = gpd.read_file(config.output_dir / "region_boundary.geojson").to_crs(4326)
    west, south, east, north = boundary.total_bounds
    canvas = DualMap(location=[(south + north) / 2, (west + east) / 2], tiles=None)
    regions = gpd.read_file(config.output_dir / "regions_display.geojson").to_crs(4326)
    images = [child for child in local.map._children.values() if isinstance(child, folium.raster_layers.ImageOverlay)]
    for side in (canvas.m1, canvas.m2):
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri, Maxar, Earthstar Geographics", name="Satélite", max_zoom=19,
        ).add_to(side)
        folium.map.CustomPane("scene-rasters", z_index=350).add_to(side)
        folium.map.CustomPane("monitor-vectors", z_index=600).add_to(side)
    folium.TileLayer(gee_rgb_url, attr="Google Earth Engine · Copernicus Sentinel-2",
                     name="GEE · composición", overlay=True, pane="scene-rasters", max_zoom=20).add_to(canvas.m1)
    if gee_nbr_url:
        folium.TileLayer(gee_nbr_url, attr="Google Earth Engine · NBR",
                         name="GEE · NBR", overlay=True, show=False, pane="scene-rasters", max_zoom=20).add_to(canvas.m1)
    for image in images:
        # Traslada las vistas reducidas ya generadas; no carga el COG completo.
        image.add_to(canvas.m2)
    for side in (canvas.m1, canvas.m2):
        folium.GeoJson(regions, name="Regiones de monitoreo", pane="monitor-vectors",
                       style_function=lambda _: {"color": "#aaaaaa", "weight": 1, "fillOpacity": 0}).add_to(side)
        folium.GeoJson(boundary, name="Región seleccionada", pane="monitor-vectors",
                       style_function=lambda _: {"color": "#ff3333", "weight": 2, "fillOpacity": 0}).add_to(side)
        folium.LayerControl(collapsed=False).add_to(side)
        side.fit_bounds([[south, west], [north, east]])
    from branca.element import Element
    canvas.m1.get_root().html.add_child(Element(
        '<div style="position:fixed;top:12px;left:10%;z-index:9999;background:white;padding:6px">'
        'Google Earth Engine (izquierda) · Descarga procesada (derecha)</div>'
    ))
    path = result.path.parent / "gee_download_comparison.html"
    canvas.save(str(path))
    print("Comparación GEE / descarga:", path)
    return canvas, path


def create_geemap_preview(result: dict, *, basemap: str = "Esri.WorldImagery"):
    """Vista GEE directa, como en el ejemplo original: addLayer y centerObject."""
    import geemap
    canvas = geemap.Map(ee_initialize=False)
    canvas.add_basemap(basemap)
    canvas.panes = {"scene-rasters": {"zIndex": 350}, "monitor-vectors": {"zIndex": 600}}
    canvas.addLayer(result["mosaic"], result["parameters"]["vis"], "GEE · composición qualityNBR")
    canvas.layers[-1].pane = "scene-rasters"
    signature = json.dumps({"view": "color", "vis": result["parameters"]["vis"]}, sort_keys=True)
    result.setdefault("_gee_tile_cache", {})[signature] = {
        "url": canvas.layers[-1].url, "created": time.monotonic(),
    }
    if result.get("tiles") is not None:
        canvas.addLayer(result["tiles"].style(color="ffff00", fillColor="00000000", width=1), {}, "Índice de teselas")
        canvas.layers[-1].pane = "monitor-vectors"
    canvas.addLayer(result["regions"].style(color="ff0000", fillColor="00000000", width=2), {}, "Región seleccionada")
    canvas.layers[-1].pane = "monitor-vectors"
    canvas.centerObject(result["regions"], 8)
    return canvas


def create_geemap_comparison(config: PipelineConfig, result: FireCompositeResult, gee_result: dict,
                             *, view: str = "color", max_pixels: int = 1536, refresh_gee: bool = False):
    """Compara teselas GEE con teselas del COG local, sin reducir toda la región a un PNG."""
    import geemap
    from ipyleaflet import SplitMapControl, WidgetControl, GeoJSON, TileLayer
    from ipywidgets import HTML
    if view not in {"color", "NBR"}:
        raise ValueError("Elija view='color' o view='NBR'.")
    params = gee_result["parameters"]
    if view == "NBR":
        gee_image = gee_result["mosaic"].select("NBR")
        gee_vis = {"min": -1, "max": 1, "palette": ["a50026", "ffffbf", "006837"]}
        right, _ = _native_raster_layer(result.path, bands=["NBR"], name="Descarga · NBR", mode="NBR")
    else:
        gee_image, gee_vis = gee_result["mosaic"], params["vis"]
        right, _ = _native_raster_layer(result.path, bands=params["vis"]["bands"], name="Descarga · color",
                                         minimum=params["vis"]["min"], maximum=params["vis"]["max"])
    canvas = geemap.Map(ee_initialize=False, lite_mode=True)
    canvas.add_basemap("Esri.WorldImagery")
    canvas.panes = {"scene-rasters": {"zIndex": 350}, "monitor-vectors": {"zIndex": 600}}
    signature = json.dumps({"view": view, "vis": gee_vis}, sort_keys=True)
    cache = gee_result.setdefault("_gee_tile_cache", {})
    cached = cache.get(signature)
    if not refresh_gee and cached and time.monotonic() - cached["created"] < 1800:
        left = TileLayer(url=cached["url"], name="GEE · " + view,
                         attribution="Google Earth Engine · Copernicus Sentinel-2", max_zoom=24)
    else:
        left = geemap.ee_tile_layer(gee_image, gee_vis, name="GEE · " + view)
        cache[signature] = {"url": left.url, "created": time.monotonic()}
    left.pane = "scene-rasters"
    canvas.add(left)
    canvas.add(right)
    canvas.add(SplitMapControl(left_layer=left, right_layer=right))
    region = load_regions(config)
    selected = region.loc[region[config.region_id_field].astype(str) == str(config.region_id)].copy()
    if selected.empty:
        raise ValueError("La región seleccionada no está en el shapefile.")
    # Simplifica solo los vectores de visualización; mantiene la región original del procesamiento.
    display_regions = region.to_crs(3857)
    display_regions.geometry = display_regions.geometry.simplify(config.map_simplify_meters, preserve_topology=True)
    display_regions = display_regions.to_crs(4326)
    for frame, label, color, weight in [(display_regions, "Regiones", "#aaaaaa", 1),
                                         (selected, "Región seleccionada", "#ff3333", 2)]:
        canvas.add(GeoJSON(data=json.loads(frame.to_json()), name=label, pane="monitor-vectors",
                           style={"color": color, "weight": weight, "fillOpacity": 0}))
    west, south, east, north = selected.total_bounds
    canvas.fit_bounds([[south, west], [north, east]])
    canvas.add(WidgetControl(widget=HTML("<b>GEE (izquierda) · Descarga (derecha)</b>"), position="bottomleft"))
    path = result.path.parent / "gee_download_geemap.html"
    canvas.to_html(str(path))
    print("Comparación geemap:", path)
    return canvas


class NotebookWorkflow:
    """Orquesta Colab desde llamadas cortas; los parámetros siguen en el notebook."""

    def __init__(self, root: Path | str | None = None, *, in_colab: bool | None = None):
        if in_colab is None:
            try:
                import google.colab  # noqa: F401
                in_colab = True
            except ImportError:
                in_colab = False
        self.in_colab = in_colab
        if in_colab:
            try:
                from google.colab import output
                output.enable_custom_widget_manager()
            except ImportError:
                pass
        self.root = Path(root) if root is not None else (Path("/content/sentinel2_temporal") if in_colab
                                                        else Path(tempfile.gettempdir()) / "sentinel2_regiones")
        self.vectors_dir = self.root / "vectores"
        self.results_dir = self.root / "resultados"
        self.tiles_path = self.vectors_dir / "teselas.zip"
        self.regions_path = self.vectors_dir / "regiones.zip"
        self.gee_result = None
        self.config = None
        self.composite = None
        self.archive_dir = None
        self._legacy_catalog_imported = False
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        print("Almacenamiento temporal:", self.root)

    def load_vectors(self, tiles_id: str, regions_id: str, *, authenticate: bool = False):
        self.tiles_path = descargar_vector_drive(tiles_id, self.tiles_path, authenticate)
        self.regions_path = descargar_vector_drive(regions_id, self.regions_path, authenticate)

    def use_drive(self, folder: Path | str, *, mount: bool = True):
        """Configura la carpeta persistente; el procesamiento continúa en disco temporal."""
        if mount and self.in_colab:
            from google.colab import drive
            drive.mount("/content/drive")
        requested = Path(folder)
        self.archive_dir = requested.parent if requested.name in {"mosaicos", "mosaics"} else requested
        self._legacy_catalog_imported = False
        for name in ("mosaics", "samples", "models", "classification"):
            (self.archive_dir / name).mkdir(parents=True, exist_ok=True)
        print("Directorio FuegoMonitor:", self.archive_dir)

    def _archive_config(self, config):
        # Lectura compatible con las versiones anteriores del almacenamiento.
        return replace(config, data_dir=self.archive_dir / config.data_dir.relative_to(self.results_dir))

    def _mosaic_paths(self, config):
        params = dict(config.workflow_parameters or {})
        for key in ("vis", "project", "tiles_asset"):
            params.pop(key, None)
        signature = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
        stem = f"mosaic_{_rf_tag(config)}_{config.source}_{signature}"
        folder = self.archive_dir / "mosaics" / config.start_date[:7]
        return folder / (stem + ".tif"), folder / (stem + ".json")

    def _publish_mosaic(self, config, result):
        path, metadata_path = self._mosaic_paths(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.unlink(missing_ok=True)
        _atomic_copy(result.path, path)
        for original, suffix in ((result.timings_path, "_scene_timings.csv"),
                                 (result.timing_summary_path, "_process_timings.json")):
            if original is not None and original.is_file():
                _atomic_copy(original, path.with_name(path.stem + suffix))
        metadata = json.loads(result.metadata_path.read_text())
        metadata.update(path=str(path), source=config.source,
                        storage={"region_id": str(config.region_id), "month": config.start_date[:7]})
        if result.timings_path is not None:
            metadata["timings_csv"] = str(path.with_name(path.stem + "_scene_timings.csv"))
        if result.timing_summary_path is not None:
            metadata["timing_summary_json"] = str(path.with_name(path.stem + "_process_timings.json"))
        temporary = metadata_path.with_suffix(".partial.json")
        temporary.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
        temporary.replace(metadata_path)
        return path

    def _archived_mosaic(self, config):
        path, metadata_path = self._mosaic_paths(config)
        if path.is_file() and metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text())
            if metadata.get("status") != "complete":
                raise ValueError("La composición guardada no terminó correctamente.")
            timings = path.with_name(path.stem + "_scene_timings.csv")
            summary = path.with_name(path.stem + "_process_timings.json")
            return FireCompositeResult(path, metadata_path, metadata.get("valid_pixels", 0),
                                       metadata.get("processed_scenes", 0), timings if timings.is_file() else None,
                                       summary if summary.is_file() else None)
        # No vuelve a descargar los mosaicos de la organización anterior.
        for root in (self.archive_dir / "mosaicos", self.archive_dir):
            old = replace(config, data_dir=root / config.data_dir.relative_to(self.results_dir))
            try:
                result = load_fire_composite(old)
            except FileNotFoundError:
                continue
            self._publish_mosaic(config, result)
            return self._archived_mosaic(config)
        raise FileNotFoundError("No hay mosaico guardado para estos parámetros.")

    @staticmethod
    def _copy_composite(result, destination):
        """Publica metadatos al final: una copia interrumpida no se considera completa."""
        destination.mkdir(parents=True, exist_ok=True)
        metadata = destination / result.metadata_path.name
        metadata.unlink(missing_ok=True)
        for path in (result.path, result.timings_path, result.timing_summary_path, result.metadata_path):
            if path is not None and path.is_file():
                temporary = destination / (path.name + ".partial")
                shutil.copy2(path, temporary)
                temporary.replace(destination / path.name)

    def _saved_composite(self, config):
        if self.archive_dir is not None:
            try:
                archived = self._archived_mosaic(config)
            except FileNotFoundError:
                pass
            else:
                try:
                    local = load_fire_composite(config)
                except (FileNotFoundError, ValueError):
                    local = None
                if local is not None and local.path.stat().st_size == archived.path.stat().st_size:
                    a = json.loads(local.metadata_path.read_text())
                    b = json.loads(archived.metadata_path.read_text())
                    if a.get("workflow_parameters") == b.get("workflow_parameters"):
                        return local
                folder = config.output_dir / f"fire_composite_{config.start_date}_{config.end_date}"
                folder.mkdir(parents=True, exist_ok=True)
                for source, target in ((archived.path, folder / "fire_quality_mosaic.tif"),
                                       (archived.timings_path, folder / "scene_timings.csv"),
                                       (archived.timing_summary_path, folder / "process_timings.json"),
                                       (archived.metadata_path, folder / "fire_quality_mosaic.json")):
                    if source is not None:
                        _atomic_copy(source, target)
                print("Mosaico recuperado de Google Drive:", archived.path)
                return load_fire_composite(config)
        return load_fire_composite(config)

    @staticmethod
    def _initialize_gee(parameters: dict):
        import ee
        try:
            ee.Initialize(project=parameters["project"])
        except ee.EEException:
            ee.Authenticate()
            ee.Initialize(project=parameters["project"])

    def preview(self, parameters: dict, *, include_metadata: bool = False, authenticate: bool = True):
        if authenticate:
            self._initialize_gee(parameters)
        result = build_gee_mosaic(parameters, include_metadata=include_metadata)
        canvas = create_geemap_preview(result)
        self.gee_result = result
        print("Nubosidad máxima:", result["parameters"]["max_cloud_cover"], "%")
        print("Bandas para descargar:", result["parameters"]["download_bands"])
        if include_metadata:
            print("Imágenes disponibles en GEE:", result["count"])
            print("Días con imágenes:", result["dates"])
        return canvas

    def _config(self, parameters: dict, source: str) -> PipelineConfig:
        if self.gee_result is None or normalize_gee_parameters(parameters) != self.gee_result["parameters"]:
            raise ValueError("Ejecute la vista de GEE con los parámetros actuales antes de descargar o comparar.")
        return config_from_gee_parameters(parameters, shapefile=self.tiles_path, regions_zip=self.regions_path,
                                           data_dir=self.results_dir, source=source)

    def download(self, parameters: dict, *, source: str = "planetary-computer", enabled: bool = False,
                  keep_inputs: bool = False, overwrite: bool = False):
        if not enabled or not self.in_colab:
            print("Active DESCARGAR_IMAGENES para procesar en Colab. La vista GEE no descarga escenas.")
            return None
        section_started = time.perf_counter()
        config = config_from_gee_parameters(parameters, shapefile=self.tiles_path, regions_zip=self.regions_path,
                                             data_dir=self.results_dir, source=source)
        if not overwrite:
            try:
                result = self._saved_composite(config)
            except FileNotFoundError:
                pass
            except ValueError as exc:
                raise ValueError(f"{exc} Active SOBRESCRIBIR_MOSAICO para regenerarlo.") from exc
            else:
                self.config, self.composite = config, result
                if self.archive_dir is not None:
                    if not self._mosaic_paths(config)[1].is_file():
                        self._publish_mosaic(config, result)
                print("Mosaico existente reutilizado; no se descargaron escenas:", result.path)
                return result
        config = self._config(parameters, source)
        selection = select_region(config)
        available = search_scenes(config, selection.region)
        print(f"Proveedor: {source}. Escenas disponibles: {len(available.selected)}.")
        print(f"Nubosidad máxima: {config.max_cloud_cover:g}%. Bandas: {config.bands}")
        if not available.selected:
            raise ValueError("No hay escenas en el proveedor con estos parámetros.")
        missing = set(config.bands) - available_bands(available.selected).keys()
        if missing:
            raise ValueError(f"Bandas no disponibles en todas las escenas: {sorted(missing)}")
        config.download_images = True
        result = build_fire_composite(config, available.selected, selection.region,
                                       resolution_m=config.workflow_parameters["resolution_m"], keep_inputs=keep_inputs,
                                       scl_exclude=tuple(config.workflow_parameters["scl_exclude"]))
        self.config, self.composite = config, result
        section_elapsed = time.perf_counter() - section_started
        summary_path = result.timing_summary_path
        summary = json.loads(summary_path.read_text())
        totals = summary["sumas_etapas_s"]
        summary["resumen_seccion_5"] = {
            "descarga_acumulada_s": totals["download_s"],
            "procesamiento_acumulado_s": totals["process_s"] + summary["finalizacion_cog_s"],
            "finalizacion_cog_s": summary["finalizacion_cog_s"],
            "tiempo_total_s": section_elapsed,
            "nota": "Descarga y procesamiento se solapan. Procesamiento incluye máscara, remuestreo, índices, composición, limpieza y COG. El total incluye selección y consulta.",
        }
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        if self.archive_dir is not None:
            print("Mosaico guardado en Google Drive:", self._publish_mosaic(config, result))
        print(f"Escenas descargadas y procesadas: {result.scene_count}.")
        print("Mosaico:", result.path)
        print("Registro por escena:", result.timings_path)
        self.show_timings()
        return result

    def show_timings(self):
        import ipywidgets as widgets
        from IPython.display import display
        if self.composite is None or self.composite.timings_path is None:
            raise ValueError("Todavía no hay un registro de procesamiento.")
        table = pd.read_csv(self.composite.timings_path)
        summary_path = getattr(self.composite, "timing_summary_path", None)
        if summary_path is not None and summary_path.is_file():
            summary = json.loads(summary_path.read_text())
            timing_summary = summary.get("resumen_seccion_5")
            if timing_summary is not None:
                for label, key in [("Descarga acumulada", "descarga_acumulada_s"),
                                    ("Procesamiento acumulado (incluye COG)", "procesamiento_acumulado_s"),
                                    ("Tiempo total de la sección 5", "tiempo_total_s")]:
                    seconds = timing_summary[key]
                    print(f"{label}: {timedelta(seconds=round(seconds))} ({seconds:.1f} s)")
                print("Descarga y procesamiento se solapan; sus tiempos acumulados no deben sumarse para obtener el total.")
        columns = [c for c in table if c in ("Escena", "Tesela", "Estado", "Nubosidad (%)", "Error") or c.endswith("(s)")]
        display(widgets.HTML(value=table[columns].round(2).to_html(index=False),
                             layout=widgets.Layout(max_height="360px", overflow="auto", width="100%")))

    def compare(self, parameters: dict, *, source: str = "planetary-computer", view: str = "color",
                max_pixels: int = 1536, refresh_gee: bool = False):
        config = config_from_gee_parameters(parameters, shapefile=self.tiles_path, regions_zip=self.regions_path,
                                             data_dir=self.results_dir, source=source)
        result = self._saved_composite(config)
        if self.gee_result is None or normalize_gee_parameters(parameters) != self.gee_result["parameters"]:
            print("Restaurando la vista de GEE para comparar con el mosaico guardado…")
            self._initialize_gee(parameters)
            self.gee_result = build_gee_mosaic(parameters, include_metadata=False)
        self.config, self.composite = config, result
        return create_geemap_comparison(config, result, self.gee_result, view=view, max_pixels=max_pixels,
                                        refresh_gee=refresh_gee)

    def _training_composite(self, parameters: dict, *, source: str = "planetary-computer",
                            mosaic_path: Path | str | None = None):
        if mosaic_path is None:
            config = config_from_gee_parameters(parameters, shapefile=self.tiles_path, regions_zip=self.regions_path,
                                                 data_dir=self.results_dir, source=source)
            try:
                composite = self._saved_composite(config)
            except FileNotFoundError as error:
                raise FileNotFoundError(
                    f"{error} Indique la ruta del GeoTIFF existente en MOSAIC_PATH. "
                    "La sección 7 no necesita ejecutar GEE ni descargar escenas."
                ) from error
        else:
            # RF usa el mosaico local indicado; no requiere reconstruir la vista GEE.
            path = Path(mosaic_path)
            metadata_path = path.with_suffix(".json")
            if not path.is_file() or not metadata_path.is_file():
                raise FileNotFoundError("Indique el mosaico COG y conserve su JSON de metadatos junto a él.")
            metadata = json.loads(metadata_path.read_text())
            if metadata.get("status") != "complete":
                raise ValueError("El mosaico indicado no terminó correctamente.")
            if metadata.get("region_id") is not None and str(metadata["region_id"]) != str(parameters["region_id"]):
                raise ValueError("El mosaico indicado corresponde a otra región; ajuste region_id.")
            config = config_from_gee_parameters(parameters, shapefile=self.tiles_path, regions_zip=self.regions_path,
                                                 data_dir=self.results_dir, source=metadata.get("source", source))
            for key in ("start_date", "end_date"):
                if metadata.get(key) is not None and metadata[key] != getattr(config, key):
                    raise ValueError("El mosaico indicado corresponde a otro período; ajuste PARAMETERS.")
            composite = FireCompositeResult(path, metadata_path, metadata.get("valid_pixels", 0),
                                             metadata.get("processed_scenes", 0))
        return config, composite

    def _rf_workspace(self, config, composite):
        root = self.archive_dir or self.root
        for name in ("samples", "models", "classification"):
            (root / name).mkdir(parents=True, exist_ok=True)
        if self.archive_dir is not None and not self._legacy_catalog_imported:
            for old_root in (root / "mosaicos", root):
                for old_rf in old_root.glob("*/*/region_*/fire_composite_*/rf"):
                    match = re.fullmatch(r"fire_composite_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", old_rf.parent.name)
                    if not match:
                        continue
                    first, last = match.group(1)[:7], match.group(2)[:7]
                    tag = old_rf.parent.parent.name + "_" + (first if first == last else first + "_" + last)
                    old_result = FireCompositeResult(old_rf.parent / "fire_quality_mosaic.tif",
                                                     old_rf.parent / "fire_quality_mosaic.json", 0, 0)
                    _prepare_rf_workspace(old_result, tag, old_rf)
                    for path in old_rf.glob(f"samples_{tag}-v*.geojson"):
                        _copy_catalog_file(path, root / "samples" / path.name)
                    for path in old_rf.glob("*.joblib"):
                        if path.name.startswith(f"random_forest_{tag}"):
                            _copy_catalog_file(path, root / "models" / f"model_{tag}-v1.joblib")
                        elif _CATALOG_PATTERN.fullmatch(path.name):
                            _copy_catalog_file(path, root / "models" / path.name)
            self._legacy_catalog_imported = True
        workspace = _prepare_rf_workspace(composite, _rf_tag(config), root / "samples")
        for path in (composite.path.parent / "rf").glob("*.joblib"):
            if path.name.startswith(f"random_forest_{_rf_tag(config)}"):
                _copy_catalog_file(path, root / "models" / f"model_{_rf_tag(config)}-v1.joblib")
            elif _CATALOG_PATTERN.fullmatch(path.name):
                if not (root / "models" / path.name).exists():
                    _atomic_copy(path, root / "models" / path.name)
        workspace.update(models=root / "models", classification=root / "classification")
        return workspace

    def training_canvas(self, parameters: dict, *, source: str = "planetary-computer", max_pixels: int = 1536,
                          mosaic_path: Path | str | None = None, rf_parameters: dict | None = None):
        config, composite = self._training_composite(parameters, source=source, mosaic_path=mosaic_path)
        workspace = self._rf_workspace(config, composite)
        metadata = json.loads(composite.metadata_path.read_text())
        with rasterio.open(composite.path) as src:
            names, _ = _rf_predictors(src, metadata)
        print("Predictores:", names)
        snapshot = json.loads(json.dumps(parameters))
        def classify_handler(model_name, retrain, mode=None, version=None, sample_name=None, algorithm="random_forest"):
            return self.classify(parameters=rf_parameters, region_parameters=snapshot, source=source,
                                 mosaic_path=composite.path, enabled=True, mode=mode or ("retrain" if retrain else "reuse"),
                                 model_name=model_name or None, sample_name=sample_name,
                                 model_version=version, algorithm=algorithm, show=False)
        canvas = create_rf_training_folium(config, composite, samples_path=workspace["samples"],
                                          archive_folder=workspace["archive"], models_folder=workspace["models"],
                                          classify_handler=classify_handler, advanced_actions=True)
        return canvas

    def classify(self, training=None, parameters: dict | None = None, *, enabled: bool = False,
                 mode: str = "reuse", region_parameters: dict | None = None,
                 source: str = "planetary-computer", mosaic_path: Path | str | None = None,
                 model_name: str | None = None, sample_name: str | None = None,
                 model_version: int | None = None, algorithm: str = "random_forest", show: bool = True):
        if not enabled:
            print("Pulse Clasificar en el canvas para ejecutar el modelo.")
            return None
        if mode not in {"reuse", "retrain", "new"}:
            raise ValueError("Elija aplicar, reentrenar o crear un modelo nuevo.")
        if region_parameters is not None:
            config, composite = self._training_composite(region_parameters, source=source, mosaic_path=mosaic_path)
        elif training is not None:
            config, composite = training.config, training.composite
        else:
            raise ValueError("Indique PARAMETERS para seleccionar región y mes.")
        workspace = self._rf_workspace(config, composite)
        catalog_samples = {p.name: p for p in _catalog_files("samples", [workspace["folder"], workspace["archive"]])}
        if sample_name:
            if sample_name not in catalog_samples:
                raise ValueError("Seleccione un archivo de muestras disponible.")
            workspace["samples"] = workspace["folder"] / sample_name
            _atomic_copy(catalog_samples[sample_name], workspace["samples"])
        models = {p.name: p for p in _catalog_files("model", [workspace["models"]])}
        if algorithm not in FIRE_CLASSIFIERS:
            raise ValueError("Seleccione un algoritmo de clasificación disponible.")
        if model_name and mode != "new":
            algorithm = _model_algorithm(model_name)
        model_tag = _model_tag(config, algorithm)
        if not model_name and mode == "reuse":
            current = [name for name in models if name.startswith(f"model_{model_tag}-v")]
            model_name = current[-1] if current else None
        if mode == "reuse" and model_name not in models:
            raise FileNotFoundError("No hay un modelo seleccionado. Elija Crear modelo nuevo para entrenar uno.")
        if mode == "retrain" and model_name:
            if model_name not in models:
                raise FileNotFoundError("El modelo seleccionado ya no está disponible.")
            if not model_name.startswith(f"model_{model_tag}-v"):
                raise ValueError("Para entrenar con otra región o período, elija Crear modelo nuevo.")
        if mode == "new" or (mode == "retrain" and not model_name):
            version = model_version if model_version is not None else _next_catalog_version("model", model_tag, [workspace["models"]])
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("La versión del modelo debe ser un entero positivo.")
            model_name = f"model_{model_tag}-v{version}.joblib"
            if model_name in models:
                raise ValueError("Ya existe esa versión. Elija otra o use Reentrenar seleccionado.")
        if mode != "reuse" and not workspace["samples"].is_file():
            raise FileNotFoundError("Dibuje o cargue muestras de ambas clases antes de entrenar.")
        result_tag = f"{_rf_tag(config)}_{config.source}__{Path(model_name).stem}"
        result = classify_fire_rf(composite.path, workspace["samples"], output_dir=workspace["folder"],
                                  parameters=parameters, model_path=models[model_name] if mode == "reuse" else None,
                                  artifact_tag=result_tag, identity=_rf_identity(config), allow_transfer=mode == "reuse",
                                  sample_name=workspace["samples"].name, model_output_name=model_name, verbose=show, algorithm=algorithm)
        metrics = json.loads(result.metrics_path.read_text())
        metrics.update(modo=mode, model_file=model_name, mosaic_file=self._mosaic_paths(config)[0].name if self.archive_dir else composite.path.name)
        result.metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
        if mode != "reuse":
            _atomic_copy(result.model_path, workspace["models"] / model_name)
            metrics = json.loads(result.metrics_path.read_text())
            model_metadata = {"model": model_name, "identity": _rf_identity(config),
                              "algorithm": metrics["algoritmo"], "predictors": metrics["predictores"], "samples_file": workspace["samples"].name,
                              "parameters": metrics["parametros_modelo"], "trained_utc": _utc_timestamp()}
            (workspace["models"] / Path(model_name).with_suffix(".json").name).write_text(
                json.dumps(model_metadata, ensure_ascii=False, indent=2))
            _atomic_copy(result.importance_path, workspace["models"] / (Path(model_name).stem + "_importance.csv"))
        for path in (result.path, result.metrics_path, result.resources_path):
            _atomic_copy(path, workspace["classification"] / path.name)
        if show:
            print("Modelo:", workspace["models"] / model_name)
            print("Clasificación:", workspace["classification"] / result.path.name)
            from IPython.display import HTML, display
            display(pd.read_csv(result.resources_path).round(2))
            canvas = create_rf_result_folium(config, composite, result)
            display(HTML(canvas.get_root().render()) if self.in_colab else canvas)
        return result




FIRE_CLASSIFIERS = {
    "random_forest": "Random Forest",
    "gaussian_nb": "Naive Bayes gaussiano",
    "logistic_regression": "Regresión logística",
    "svm": "SVM (RBF)",
    "extra_trees": "Extra Trees",
    "gradient_boosting": "Gradient Boosting",
}


def _fire_estimator(algorithm, config):
    from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, GradientBoostingClassifier
    from sklearn.naive_bayes import GaussianNB
    from sklearn.linear_model import LogisticRegression
    from sklearn.svm import SVC
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    if algorithm not in FIRE_CLASSIFIERS:
        raise ValueError("Seleccione un algoritmo de clasificación disponible.")
    options = dict(config.classifier_parameters.get(algorithm, {}))
    if algorithm in {"random_forest", "extra_trees"}:
        defaults = dict(n_estimators=config.n_estimators, max_depth=config.max_depth,
                        min_samples_leaf=config.min_samples_leaf, n_jobs=config.n_jobs,
                        class_weight="balanced", random_state=config.random_state)
        estimator = RandomForestClassifier if algorithm == "random_forest" else ExtraTreesClassifier
    elif algorithm == "gradient_boosting":
        defaults = dict(n_estimators=config.n_estimators, max_depth=3,
                        min_samples_leaf=config.min_samples_leaf, random_state=config.random_state)
        estimator = GradientBoostingClassifier
    elif algorithm == "gaussian_nb":
        defaults, estimator = {}, GaussianNB
    elif algorithm == "logistic_regression":
        defaults, estimator = dict(max_iter=1000, class_weight="balanced", random_state=config.random_state), LogisticRegression
    else:
        defaults, estimator = dict(kernel="rbf", C=1.0, gamma="scale", class_weight="balanced", cache_size=128), SVC
    defaults.update(options)
    model = estimator(**defaults)
    if algorithm in {"gaussian_nb", "logistic_regression", "svm"}:
        model = Pipeline([("scaler", StandardScaler()), ("classifier", model)])
    return model


def _write_classifier_importance(model, names, path):
    # No inventa importancias para Naive Bayes ni SVM con kernel.
    estimator = model.named_steps["classifier"] if hasattr(model, "named_steps") else model
    if hasattr(estimator, "feature_importances_"):
        values, method = estimator.feature_importances_, "Importancia de árboles"
    elif hasattr(estimator, "coef_"):
        values, method = np.abs(estimator.coef_[0]), "Magnitud del coeficiente estandarizado"
    else:
        values, method = np.full(len(names), np.nan), "No disponible para este estimador"
    pd.DataFrame({"Banda": names, "Importancia": values, "Método": method}).to_csv(path, index=False)


def _classifier_parameters(model):
    return {key: value if value is None or isinstance(value, (str, int, float, bool)) else repr(value)
            for key, value in model.get_params().items()}


@dataclass
class RFConfig:
    """Parámetros de RF y límites de muestreo para Colab."""
    n_estimators: int = 200
    max_depth: int | None = 20
    min_samples_leaf: int = 2
    max_samples_per_class: int = 5000
    min_samples_per_class: int = 10
    random_state: int = 42
    n_jobs: int = 2
    block_size: int = 512
    classifier_parameters: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.classifier_parameters, dict) or any(
                key not in FIRE_CLASSIFIERS or not isinstance(value, dict) for key, value in self.classifier_parameters.items()):
            raise ValueError("classifier_parameters debe contener un diccionario por algoritmo.")
        if (self.n_estimators < 1 or self.min_samples_leaf < 1 or
                not 1 <= self.min_samples_per_class <= self.max_samples_per_class or
                not 1 <= self.n_jobs <= 8 or self.block_size < 16 or self.block_size % 16 or
                (self.max_depth is not None and self.max_depth < 1)):
            raise ValueError("Revise árboles, profundidad, muestras, hilos (1–8) y bloques (múltiplos de 16).")


@dataclass
class RFResult:
    path: Path
    model_path: Path
    samples_path: Path
    metrics_path: Path
    resources_path: Path
    importance_path: Path


class _RFResourceMonitor:
    """Muestrea RSS del proceso Python cada 0,1 s, también en los extremos de cada etapa."""
    def __enter__(self):
        import psutil
        self.process = psutil.Process()
        self.started = time.perf_counter()
        self.initial = self.process.memory_info().rss
        self.peak = self.initial
        self.rows = []
        self.active = None
        self.lock, self.stop = Lock(), Event()
        self.thread = Thread(target=self._poll, daemon=True)
        self.thread.start()
        return self

    def sample(self):
        rss = self.process.memory_info().rss
        with self.lock:
            self.peak = max(self.peak, rss)
            if self.active is not None:
                self.active["ram_pico_mb"] = max(self.active["ram_pico_mb"], rss / 1024**2)
        return rss

    def _poll(self):
        while not self.stop.wait(0.1):
            self.sample()

    @contextmanager
    def phase(self, name):
        rss = self.sample() / 1024**2
        started = time.perf_counter()
        row = {"etapa": name, "ram_inicio_mb": rss, "ram_pico_mb": rss}
        with self.lock:
            self.active = row
        try:
            yield
        finally:
            row["ram_final_mb"] = self.sample() / 1024**2
            row["duracion_s"] = time.perf_counter() - started
            row["incremento_pico_mb"] = max(0, row["ram_pico_mb"] - rss)
            with self.lock:
                self.active = None
                self.rows.append(row)

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self.final = self.sample()
        self.elapsed = time.perf_counter() - self.started


def _rf_predictors(src, metadata: dict) -> tuple[list[str], list[int]]:
    """Solo bandas espectrales y NBR; excluye SCL, auxiliares y procedencia."""
    aliases = {"swir16": "swir1", "swir22": "swir2"}
    spectral = {_PC_BANDS[b] for b in _PC_BANDS if b.startswith("B")}
    spectral = {aliases.get(b, b) for b in spectral}
    names = [name for name in src.descriptions if name in spectral]
    expected = (metadata.get("workflow_parameters") or {}).get("download_bands", [])
    expected = {aliases.get(b, b) for b in expected if aliases.get(b, b) in spectral}
    missing = expected - set(names)
    if missing:
        raise ValueError(f"El mosaico antiguo no conservó estas bandas: {sorted(missing)}. Genérelo de nuevo con el módulo actualizado.")
    if not {"red", "nir", "swir1", "swir2", "NBR"} <= set(src.descriptions):
        raise ValueError("El mosaico debe contener rojo, NIR, SWIR1, SWIR2 y NBR.")
    names.append("NBR")
    return names, [src.descriptions.index(name) + 1 for name in names]


def _rf_samples_frame(samples) -> gpd.GeoDataFrame:
    if isinstance(samples, gpd.GeoDataFrame):
        frame = samples.copy()
    elif isinstance(samples, (str, Path)):
        frame = gpd.read_file(samples)
    else:
        frame = gpd.GeoDataFrame.from_features(samples["features"], crs=4326)
    if frame.empty or frame.crs is None or "class_id" not in frame:
        raise ValueError("Dibuje muestras con class_id: 0 = no fuego, 1 = fuego.")
    if frame["class_id"].isna().any() or not frame["class_id"].isin([0, 1]).all():
        raise ValueError("Las clases admitidas son 0 = no fuego y 1 = fuego.")
    if set(frame["class_id"]) != {0, 1}:
        raise ValueError("Se necesitan muestras de ambas clases: fuego y no fuego.")
    if (not frame.is_valid.all() or frame.geometry.is_empty.any() or frame.geometry.isna().any() or
            not frame.geometry.geom_type.isin(["Point", "Polygon", "MultiPolygon"]).all()):
        raise ValueError("Use puntos o polígonos válidos para las muestras.")
    return frame


def _rf_extract_samples(src, samples, indexes, config):
    """Muestreo uniforme con prioridades aleatorias; memoria limitada por clase y bloque."""
    samples = samples.to_crs(src.crs).copy()
    for index, geom in samples.geometry.items():
        if geom.geom_type == "Point":
            row, col = src.index(geom.x, geom.y)
            samples.at[index, "geometry"] = box(*rasterio.windows.bounds(Window(col, row, 1, 1), src.transform))
    samples = samples.reset_index(drop=True)
    blocks = set()
    for geom in samples.geometry:
        window = rasterio.windows.from_bounds(*geom.bounds, src.transform)
        left, top = max(0, int(np.floor(window.col_off))), max(0, int(np.floor(window.row_off)))
        right, bottom = min(src.width, int(np.ceil(window.col_off + window.width))), min(src.height, int(np.ceil(window.row_off + window.height)))
        for row in range(top // config.block_size * config.block_size, bottom, config.block_size):
            for col in range(left // config.block_size * config.block_size, right, config.block_size):
                blocks.add((row, col))
    rng = np.random.default_rng(config.random_state)
    reservoirs = {label: (np.empty((0, len(indexes)), dtype="float32"), np.empty(0)) for label in (0, 1)}
    counts = {0: 0, 1: 0}
    spatial_index = samples.sindex
    for row, col in sorted(blocks):
        window = Window(col, row, min(config.block_size, src.width - col), min(config.block_size, src.height - row))
        bounds = rasterio.windows.bounds(window, src.transform)
        subset = samples.iloc[spatial_index.query(box(*bounds), predicate="intersects")]
        shape_window = (int(window.height), int(window.width))
        masks = {}
        for label in (0, 1):
            geometries = subset.loc[subset.class_id == label].geometry.tolist()
            masks[label] = (geometry_mask(geometries, out_shape=shape_window, transform=src.window_transform(window), invert=True)
                            if geometries else np.zeros(shape_window, dtype=bool))
        if np.any(masks[0] & masks[1]):
            raise ValueError("Hay píxeles etiquetados a la vez como fuego y no fuego; corrija las muestras superpuestas.")
        data = src.read(indexes, window=window, masked=True).astype("float32").filled(np.nan)
        valid = np.isfinite(data).all(axis=0)
        for label in (0, 1):
            values = np.moveaxis(data, 0, -1)[valid & masks[label]]
            counts[label] += len(values)
            if not len(values):
                continue
            priorities = rng.random(len(values))
            previous_x, previous_p = reservoirs[label]
            combined_x, combined_p = np.concatenate([previous_x, values]), np.concatenate([previous_p, priorities])
            size = min(config.max_samples_per_class, len(combined_p))
            keep = np.argpartition(combined_p, size - 1)[:size]
            reservoirs[label] = combined_x[keep], combined_p[keep]
    for label in (0, 1):
        if len(reservoirs[label][0]) < config.min_samples_per_class:
            raise ValueError(f"La clase {label} tiene {len(reservoirs[label][0])} píxeles válidos; se requieren al menos {config.min_samples_per_class}. Añada muestras dentro de píxeles válidos del mosaico.")
    x = np.concatenate([reservoirs[label][0] for label in (0, 1)])
    y = np.concatenate([np.full(len(reservoirs[label][0]), label, dtype="uint8") for label in (0, 1)])
    return x, y, counts


def classify_fire_rf(mosaic_path: Path, samples=None, *, output_dir: Path | None = None,
                       parameters: RFConfig | dict | None = None, model_path: Path | None = None,
                       artifact_tag: str | None = None, identity: dict | None = None, sample_name: str | None = None,
                       model_output_name: str | None = None, allow_transfer: bool = False, verbose: bool = True,
                       algorithm: str = "random_forest") -> RFResult:
    """Entrena un clasificador con muestras propias y predice por bloques: 0 no fuego, 1 fuego, 255 sin datos."""
    import joblib
    if algorithm not in FIRE_CLASSIFIERS:
        raise ValueError("Seleccione un algoritmo de clasificación disponible.")
    config = RFConfig(**parameters) if isinstance(parameters, dict) else (parameters or RFConfig())
    mosaic_path = Path(mosaic_path)
    output_dir = Path(output_dir) if output_dir is not None else mosaic_path.parent / "rf"
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = mosaic_path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else {}
    sample_tag = artifact_tag or re.sub(r"[^a-zA-Z0-9_-]", "_",
                                        f"region_{metadata.get('region_id', 'unknown')}_{metadata.get('start_date', 'unknown')[:7]}")
    suffix = "_" + artifact_tag if artifact_tag else ""
    result = RFResult(output_dir / f"fire_rf{suffix}.tif", output_dir / (model_output_name or f"model_{artifact_tag or 'region_unknown_unknown'}-v1.joblib"),
                      output_dir / (sample_name or f"samples_{sample_tag}-v1.geojson"), output_dir / f"rf_metrics{suffix}.json",
                      output_dir / f"rf_resources{suffix}.csv", output_dir / f"feature_importance{suffix}.csv")
    metrics = {"estado": "en_curso", "clases": {"0": "No fuego", "1": "Fuego", "255": "Sin datos"},
               "parametros": dict(vars(config)), "mosaico": str(mosaic_path),
               "ram_medida": "RSS del proceso Python completo, muestreado cada 0,1 s; incluye otras celdas y bibliotecas.",
               "validacion": "No se calculó exactitud independiente; las muestras se usan para entrenar."}
    work_path = output_dir / f".rf_{uuid4().hex}.tif"
    monitor = _RFResourceMonitor()
    try:
        with monitor, rasterio.Env(GDAL_CACHEMAX=64 * 1024**2), rasterio.open(mosaic_path) as src:
            names, indexes = _rf_predictors(src, metadata)
            metrics["predictores"] = names
            if verbose:
                print("Predictores:", names)
            metrics["modo"] = "reuse" if model_path is not None else "retrain"
            metrics["identidad"] = identity
            if model_path is not None:
                with monitor.phase("Carga del modelo"):
                    bundle = joblib.load(model_path)
                    if bundle.get("predictors") != names:
                        raise ValueError("Las bandas del modelo no coinciden con el mosaico. Reentrene RF.")
                    if not allow_transfer and identity is not None and bundle.get("identity") != identity:
                        raise ValueError("El modelo pertenece a otra región, período o fuente. Reentrene RF.")
                    metrics["identidad_modelo"] = bundle.get("identity")
                    metrics["samples_file"] = bundle.get("samples_file")
                    model = bundle["model"]
                    if set(model.classes_) != {0, 1} or model.n_features_in_ != len(names):
                        raise ValueError("El modelo guardado no corresponde a clasificación binaria fuego/no fuego.")
                    algorithm = bundle.get("algorithm", "random_forest")
                    if algorithm not in FIRE_CLASSIFIERS:
                        raise ValueError("El modelo usa un algoritmo no disponible.")
                    if "n_jobs" in model.get_params():
                        model.set_params(n_jobs=config.n_jobs)
                    if Path(model_path).resolve() != result.model_path.resolve():
                        _atomic_joblib(bundle, result.model_path)
                    _write_classifier_importance(model, names, result.importance_path)
            else:
                with monitor.phase("Extracción de muestras"):
                    frame = _rf_samples_frame(samples)
                    if not isinstance(samples, (str, Path)) or Path(samples).resolve() != result.samples_path.resolve():
                        frame.to_crs(4326).to_file(result.samples_path, driver="GeoJSON")
                    metrics["samples_file"] = result.samples_path.name
                    x, y, available = _rf_extract_samples(src, frame, indexes, config)
                    metrics["pixeles_disponibles_por_clase"] = {str(k): v for k, v in available.items()}
                    metrics["pixeles_entrenamiento_por_clase"] = {str(k): int(np.count_nonzero(y == k)) for k in (0, 1)}
                with monitor.phase("Entrenamiento RF" if algorithm == "random_forest" else "Entrenamiento " + FIRE_CLASSIFIERS[algorithm]):
                    if verbose:
                        print("Entrenando " + FIRE_CLASSIFIERS[algorithm] + "…")
                    model = _fire_estimator(algorithm, config)
                    model.fit(x, y)
                    _atomic_joblib({"model": model, "predictors": names, "classes": {0: "No fuego", 1: "Fuego"},
                                "identity": identity, "samples_file": result.samples_path.name, "algorithm": algorithm}, result.model_path)
                    _write_classifier_importance(model, names, result.importance_path)
                    del x, y
            metrics["algoritmo"] = algorithm
            metrics["nombre_algoritmo"] = FIRE_CLASSIFIERS[algorithm]
            metrics["parametros_modelo"] = _classifier_parameters(model)
            profile = src.profile.copy()
            profile.update(driver="GTiff", count=1, dtype="uint8", nodata=255, tiled=True,
                           blockxsize=config.block_size, blockysize=config.block_size,
                           compress="DEFLATE", predictor=1, BIGTIFF="IF_SAFER")
            metrics["pixeles_clasificados"] = 0
            metrics["pixeles_fuego"] = 0
            with monitor.phase("Clasificación por bloques"), rasterio.open(work_path, "w", **profile) as dst:
                dst.set_band_description(1, "fire_class")
                dst.update_tags(classes="0=No fuego; 1=Fuego; 255=Sin datos")
                total_blocks = int(np.ceil(src.width / config.block_size) * np.ceil(src.height / config.block_size))
                with tqdm(total=total_blocks, desc="Clasificación", unit="bloque", mininterval=1, disable=not verbose) as progress:
                    for row in range(0, src.height, config.block_size):
                        for col in range(0, src.width, config.block_size):
                            window = Window(col, row, min(config.block_size, src.width - col), min(config.block_size, src.height - row))
                            data = src.read(indexes, window=window, masked=True).astype("float32").filled(np.nan)
                            valid = np.isfinite(data).all(axis=0)
                            prediction = np.full(valid.shape, 255, dtype="uint8")
                            if valid.any():
                                prediction[valid] = model.predict(np.moveaxis(data, 0, -1)[valid]).astype("uint8")
                            metrics["pixeles_clasificados"] += int(valid.sum())
                            metrics["pixeles_fuego"] += int(np.count_nonzero(prediction == 1))
                            dst.write(prediction, 1, window=window)
                            progress.update(1)
            with monitor.phase("Exportación COG"):
                partial = output_dir / f".rf_cog_{uuid4().hex}.tif"
                try:
                    copy_raster(work_path, partial, driver="COG", COMPRESS="DEFLATE", BLOCKSIZE=512,
                                OVERVIEW_RESAMPLING="NEAREST", BIGTIFF="IF_SAFER")
                    partial.replace(result.path)
                finally:
                    partial.unlink(missing_ok=True)
            metrics["estado"] = "completado"
    except BaseException as error:
        metrics.update(estado="error", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        work_path.unlink(missing_ok=True)
        metrics.update(tiempo_total_s=monitor.elapsed, ram_inicio_mb=monitor.initial / 1024**2,
                       ram_pico_mb=monitor.peak / 1024**2, ram_final_mb=monitor.final / 1024**2,
                       incremento_ram_pico_mb=max(0, monitor.peak - monitor.initial) / 1024**2,
                       etapas=monitor.rows)
        result.metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
        pd.DataFrame(monitor.rows).to_csv(result.resources_path, index=False)
    if verbose:
        print(f"{FIRE_CLASSIFIERS[algorithm]}: {monitor.elapsed:.1f} s; RAM inicial: {monitor.initial / 1024**2:.1f} MB; "
              f"pico RSS: {monitor.peak / 1024**2:.1f} MB; incremento: {metrics['incremento_ram_pico_mb']:.1f} MB.")
        print("Clasificación:", result.path)
        print("Tiempos y RAM por etapa:", result.resources_path)
    return result


def _local_mosaic_geemap(config: PipelineConfig, result: FireCompositeResult, max_pixels: int = 1536):
    import geemap
    vis = (config.workflow_parameters or {}).get("vis", {"bands": ["swir1", "nir", "red"], "min": 0.03, "max": 0.4})
    layer, bounds = _native_raster_layer(result.path, bands=vis["bands"], name="Mosaico procesado · resolución original",
                                         minimum=vis["min"], maximum=vis["max"])
    canvas = geemap.Map(ee_initialize=False, lite_mode=True, height="650px")
    canvas.add_basemap("Esri.WorldImagery")
    canvas.panes = {"scene-rasters": {"zIndex": 350}, "monitor-vectors": {"zIndex": 600}}
    canvas.add(layer)
    region = load_regions(config)
    selected = region.loc[region[config.region_id_field].astype(str) == str(config.region_id)]
    from ipyleaflet import GeoJSON, LayersControl
    canvas.add(GeoJSON(data=json.loads(selected.to_json()), name="Región seleccionada", pane="monitor-vectors",
                       style={"color": "#ffffff", "weight": 2, "fillOpacity": 0}))
    canvas.add(LayersControl(position="topright"))
    canvas.fit_bounds(bounds)
    return canvas


def _atomic_copy(source: Path, destination: Path):
    destination = Path(destination)
    if Path(source).resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_joblib(bundle, destination: Path):
    import joblib
    temporary = destination.with_name(destination.name + ".partial")
    try:
        joblib.dump(bundle, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _rf_tag(config):
    first, last = config.start_date[:7], config.end_date[:7]
    period = first if first == last else first + "_" + last
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"region_{config.region_id}_{period}")


def _model_tag(config, algorithm="random_forest"):
    return _rf_tag(config) + ("" if algorithm == "random_forest" else "_" + algorithm)


def _model_algorithm(filename):
    base = filename.rsplit("-v", 1)[0]
    return next((key for key in FIRE_CLASSIFIERS if base.endswith("_" + key)), "random_forest")


def _rf_identity(config):
    return {"region_id": str(config.region_id), "start_date": config.start_date,
            "end_date": config.end_date, "source": config.source}


def _copy_catalog_file(source, destination):
    """Importa archivos antiguos sin reemplazar un archivo distinto del catálogo."""
    source, destination = Path(source), Path(destination)
    if destination.is_file():
        with source.open('rb') as a, destination.open('rb') as b:
            source_digest = hashlib.file_digest(a, 'sha256').digest()
            if source_digest == hashlib.file_digest(b, 'sha256').digest():
                return destination
        match = re.search(r"-v(\d+)$", destination.stem)
        if match is None:
            raise ValueError(f"Ya existe un archivo diferente: {destination.name}")
        base = destination.stem[:match.start()]
        for candidate in destination.parent.glob(f"{base}-v*{destination.suffix}"):
            with candidate.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').digest() == source_digest:
                    return candidate
        version = int(match.group(1)) + 1
        while destination.with_name(f"{base}-v{version}{destination.suffix}").exists():
            version += 1
        destination = destination.with_name(f"{base}-v{version}{destination.suffix}")
    _atomic_copy(source, destination)
    return destination


_CATALOG_PATTERN = re.compile(r"^(samples|model)_region_([^_]+)_(\d{4}-\d{2}(?:_\d{4}-\d{2})?)(?:_(?:gaussian_nb|logistic_regression|svm|extra_trees|gradient_boosting))?-v([1-9]\d*)\.(geojson|joblib)$")


def _catalog_files(kind, folders, region_id=None):
    files = {}
    for folder in folders:
        if folder is None or not Path(folder).is_dir():
            continue
        for path in Path(folder).glob("*.geojson" if kind == "samples" else "*.joblib"):
            match = _CATALOG_PATTERN.fullmatch(path.name)
            if match and match.group(1) == kind and (region_id is None or match.group(2) == str(region_id)):
                files[path.name] = path
    return sorted(files.values(), key=lambda p: (p.name.rsplit('-v', 1)[0], int(_CATALOG_PATTERN.fullmatch(p.name).group(4))))


def _next_catalog_version(kind, tag, folders):
    prefix = f"{kind}_{tag}-v"
    versions = [int(_CATALOG_PATTERN.fullmatch(p.name).group(4))
                for p in _catalog_files(kind, folders) if p.name.startswith(prefix)]
    return max(versions, default=0) + 1


def _write_rf_sample_selection(folder, filename, archive=None):
    tag = filename[len("samples_"):].rsplit("-v", 1)[0]
    pointer = folder / f"selection_{tag}.json"
    temporary = pointer.with_suffix(".tmp.json")
    temporary.write_text(json.dumps({"filename": filename}))
    temporary.replace(pointer)
    if archive is not None:
        _atomic_copy(pointer, archive / pointer.name)


def _prepare_rf_workspace(composite, tag, archive_folder=None):
    folder = composite.path.parent / "rf"
    folder.mkdir(parents=True, exist_ok=True)
    archive = Path(archive_folder) if archive_folder else None
    if archive is not None:
        archive.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"samples_{re.escape(tag)}-v([1-9]\d*)\.geojson")
    folders = [folder] + ([archive] if archive is not None and archive != folder else [])
    versions = {}
    for directory in folders:
        for path in directory.glob("*.geojson"):
            match = pattern.fullmatch(path.name)
            if match:
                versions[int(match.group(1))] = path
    preferred = None
    # Migra los nombres anteriores; si son distintos conserva cada conjunto en una versión.
    for old_name in (f"samples_{tag}.geojson", "training_samples.geojson", f"training_samples_used_{tag}.geojson"):
        for directory in reversed(folders):
            old = directory / old_name
            if not old.is_file():
                continue
            data = json.loads(old.read_text())
            same = next((n for n, path in versions.items() if json.loads(path.read_text()) == data), None)
            number = same if same is not None else max(versions, default=0) + 1
            filename = f"samples_{tag}-v{number}.geojson"
            if same is None:
                destination = directory / filename
                _atomic_copy(old, destination)
                versions[number] = destination
            if preferred is None and old_name != f"training_samples_used_{tag}.geojson":
                preferred = filename
            old.unlink()
    for path in versions.values():
        _atomic_copy(path, folder / path.name)
        if archive is not None:
            _atomic_copy(folder / path.name, archive / path.name)
    pointer_name = f"selection_{tag}.json"
    pointer_dir = archive if archive is not None and (archive / pointer_name).is_file() else folder
    pointer = pointer_dir / pointer_name
    if not pointer.is_file() and (folder / "rf_samples.json").is_file():
        pointer = folder / "rf_samples.json"
    active = json.loads(pointer.read_text()).get("filename") if pointer.is_file() else None
    if active not in {p.name for p in versions.values()}:
        active = preferred or (f"samples_{tag}-v{max(versions)}.geojson" if versions else f"samples_{tag}-v1.geojson")
    _write_rf_sample_selection(folder, active, archive)
    model = folder / f"random_forest_{tag}.joblib"
    return {"folder": folder, "archive": archive, "samples": folder / active, "model": model}


def _available_rf_models(composite, tag, archive_folder=None):
    paths = {}
    for folder in (composite.path.parent / "rf", archive_folder):
        if folder is not None and Path(folder).is_dir():
            for path in Path(folder).glob(f"random_forest_{tag}*.joblib"):
                paths[path.name] = path
    return [paths[name] for name in sorted(paths)]


def _available_rf_samples(composite, tag, archive_folder=None):
    paths = {}
    pattern = re.compile(rf"samples_{re.escape(tag)}-v([1-9]\d*)\.geojson")
    for folder in (composite.path.parent / "rf", archive_folder):
        if folder is not None and Path(folder).is_dir():
            for path in Path(folder).glob("*.geojson"):
                if pattern.fullmatch(path.name):
                    paths[path.name] = path
    return sorted(paths.values(), key=lambda path: int(pattern.fullmatch(path.name).group(1)))


def _add_folium_local_raster(canvas, path, bands, name, *, mode="color", minimum=.03, maximum=.4,
                              pane="scene-rasters", opacity=1):
    with rasterio.open(path) as src:
        spec = {"path": str(Path(path).resolve()), "indexes": [src.descriptions.index(b) + 1 for b in bands],
                "mode": mode, "min": minimum, "max": maximum}
        bounds = _leaflet_bounds(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
    try:
        from google.colab import output
    except ImportError:
        global _NATIVE_TILE_SERVICE
        if _NATIVE_TILE_SERVICE is None:
            _NATIVE_TILE_SERVICE = _NativeTileService()
        folium.TileLayer(_NATIVE_TILE_SERVICE.register(spec), name=name, attr="Ráster local", pane=pane,
                         opacity=opacity, max_zoom=24, max_native_zoom=24).add_to(canvas)
    else:
        from IPython.display import JSON
        import base64
        callback = "fuego.raster." + uuid4().hex
        def tile(z, x, y):
            return JSON({"png": base64.b64encode(_render_native_tile(spec, int(z), int(x), int(y))).decode("ascii")})
        output.register_callback(callback, tile)
        group = folium.FeatureGroup(name=name, overlay=True, show=True).add_to(canvas)
        layer = MacroElement()
        layer.callback, layer.pane, layer.opacity = callback, pane, opacity
        layer._template = Template(r"""
        {% macro script(this, kwargs) %}
        (function() {
          const pending=[]; let busy=false;
          async function pump() {
            if(busy) return; busy=true;
            while(pending.length) {
              const task=pending.shift();
              if(!task.img.isConnected) {task.done(null,task.img); continue;}
              try {
                const response=await google.colab.kernel.invokeFunction({{this.callback|tojson}},[task.coords.z,task.coords.x,task.coords.y],{});
                task.img.onload=()=>task.done(null,task.img);
                task.img.onerror=()=>task.done(Error('No se pudo mostrar la tesela'),task.img);
                task.img.src='data:image/png;base64,'+response.data['application/json'].png;
              } catch(error) {console.error(error); task.done(error,task.img);}
            }
            busy=false;
          }
          const Raster=L.GridLayer.extend({createTile:function(coords,done) {
            const img=document.createElement('img'); img.width=256; img.height=256; img.alt='';
            pending.push({img:img,coords:coords,done:done}); setTimeout(pump,0); return img;
          }});
          new Raster({pane:{{this.pane|tojson}},opacity:{{this.opacity|tojson}},maxZoom:24,keepBuffer:0,updateWhenIdle:true}).addTo({{this._parent.get_name()}});
        })();
        {% endmacro %}
        """)
        layer.add_to(group)
    return bounds


def create_rf_result_folium(config, composite, classification):
    """Mosaico y clasificación RF en Folium, por teselas y con capas conmutables."""
    canvas = folium.Map(tiles=None, height=650, max_zoom=24)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri, Maxar, Earthstar Geographics", name="Satélite", max_zoom=24).add_to(canvas)
    for name, order in (("scene-rasters", 350), ("rf-classes", 400), ("monitor-vectors", 600)):
        folium.map.CustomPane(name, z_index=order).add_to(canvas)
    vis = (config.workflow_parameters or {}).get("vis", {"bands": ["swir1", "nir", "red"], "min": .03, "max": .4})
    bounds = _add_folium_local_raster(canvas, composite.path, vis["bands"], "Mosaico procesado",
                                      minimum=vis["min"], maximum=vis["max"])
    _add_folium_local_raster(canvas, classification.path, ["fire_class"], "RF · fuego",
                             mode="fire_only", pane="rf-classes", opacity=.7)
    regions = load_regions(config)
    selected = regions.loc[regions[config.region_id_field].astype(str) == str(config.region_id)]
    folium.GeoJson(json.loads(selected.to_crs(4326).to_json()), name="Región seleccionada", pane="monitor-vectors",
                   style_function=lambda _: {"color": "#ffffff", "weight": 2, "fillOpacity": 0}).add_to(canvas)
    canvas.get_root().html.add_child(folium.Element(
        '<div style="position:fixed;bottom:20px;left:15px;z-index:1000;background:white;color:#222;padding:10px">'
        '<b style="color:#ff261a">■ Fuego (1)</b></div>'))
    folium.LayerControl(collapsed=False).add_to(canvas)
    canvas.fit_bounds(bounds)
    canvas.save(str(classification.path.with_suffix(".html")))
    return canvas


class RFTrainingFoliumCanvas:
    """Canvas Folium con muestras persistentes y teselas locales de memoria acotada."""
    def __init__(self, config: PipelineConfig, composite: FireCompositeResult, *, samples_path=None, archive_folder=None,
                 models_folder=None, classify_handler=None, advanced_actions=False):
        from folium.plugins import Draw
        self.config, self.composite = config, composite
        self.samples_path = Path(samples_path) if samples_path else _prepare_rf_workspace(composite, _rf_tag(config), archive_folder)["samples"]
        self.archive_folder = Path(archive_folder) if archive_folder else None
        self.classify_handler = classify_handler
        self.models_folder = Path(models_folder) if models_folder else composite.path.parent / "rf"
        self.advanced_actions = advanced_actions
        self.job_lock = Lock()
        self.job = {"state": "idle"}
        self.classification = None
        self.results = {}
        self.result_url = ""
        self.samples_path.parent.mkdir(parents=True, exist_ok=True)
        self.features = json.loads(self.samples_path.read_text())["features"] if self.samples_path.is_file() else []
        vis = (config.workflow_parameters or {}).get("vis", {"bands": ["swir1", "nir", "red"], "min": .03, "max": .4})
        with rasterio.open(composite.path) as src:
            spec = {"path": str(composite.path.resolve()), "indexes": [src.descriptions.index(b) + 1 for b in vis["bands"]],
                    "mode": "color", "min": vis["min"], "max": vis["max"]}
            bounds = _leaflet_bounds(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
        canvas = self.map = folium.Map(tiles=None, height=650, max_zoom=24)
        folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                         attr="Esri, Maxar, Earthstar Geographics", name="Satélite", max_zoom=24).add_to(canvas)
        folium.map.CustomPane("scene-rasters", z_index=350).add_to(canvas)
        folium.map.CustomPane("rf-classes", z_index=400).add_to(canvas)
        folium.map.CustomPane("monitor-vectors", z_index=600).add_to(canvas)
        folium.map.CustomPane("rf-samples", z_index=610, pointer_events=True).add_to(canvas)
        regions = load_regions(config)
        selected = regions.loc[regions[config.region_id_field].astype(str) == str(config.region_id)]
        if selected.empty:
            raise ValueError("La región seleccionada no está en el shapefile.")
        folium.GeoJson(json.loads(selected.to_crs(4326).to_json()), name="Región seleccionada", pane="monitor-vectors",
                       style_function=lambda _: {"color": "#ffffff", "weight": 2, "fillOpacity": 0}).add_to(canvas)
        try:
            from google.colab import output
        except ImportError:
            output = None
        self.in_colab = output is not None
        self.callback_id = "fuego.rf." + uuid4().hex
        mosaic_layer = folium.FeatureGroup(name="Mosaico procesado", overlay=True, show=True).add_to(canvas)
        if output is not None:
            # HTML directo en la salida de Colab: no usa widgets ni espera proxyPort.
            from IPython.display import JSON
            output.register_callback(self.callback_id + ".save", lambda data, version=None: JSON(self.save_samples(data, version)))
            output.register_callback(self.callback_id + ".load", lambda filename=None: JSON(self.load_samples(filename)))
            output.register_callback(self.callback_id + ".export", lambda version: JSON(self.export_samples(version)))
            output.register_callback(self.callback_id + ".start", lambda model, action=False, version=None, samples=None, algorithm="random_forest": JSON(self.start_classification(model, action, version, samples, algorithm)))
            output.register_callback(self.callback_id + ".catalog", lambda: JSON(self.catalog()))
            output.register_callback(self.callback_id + ".sample_counts", lambda filename: JSON(self.sample_counts(filename)))
            output.register_callback(self.callback_id + ".status", lambda: JSON(self.classification_status()))
            output.register_callback(self.callback_id + ".result_tile", lambda z, x, y, result_id=None: JSON(self.result_tile(z, x, y, result_id)))
            def tile(z, x, y):
                import base64
                return JSON({"png": base64.b64encode(_render_native_tile(spec, int(z), int(x), int(y))).decode("ascii")})
            output.register_callback(self.callback_id + ".tile", tile)
            tile_url, save_url = "", ""
        else:
            global _NATIVE_TILE_SERVICE
            if _NATIVE_TILE_SERVICE is None:
                _NATIVE_TILE_SERVICE = _NativeTileService()
            tile_url = _NATIVE_TILE_SERVICE.register(spec)
            save_url = _NATIVE_TILE_SERVICE.register_samples(self._sample_request)
            folium.TileLayer(tile_url, attr="Mosaico procesado local", name="Mosaico procesado", pane="scene-rasters",
                             max_zoom=24, max_native_zoom=24).add_to(mosaic_layer)
        drawn = folium.FeatureGroup(name="Muestras RF", pane="rf-samples").add_to(canvas)
        locale = MacroElement()
        locale._template = Template("""
        {% macro script(this, kwargs) %}
        Object.assign(L.drawLocal.draw.toolbar.buttons, {polygon:'Dibujar polígono',rectangle:'Dibujar rectángulo',marker:'Dibujar punto'});
        Object.assign(L.drawLocal.draw.toolbar.actions, {title:'Cancelar dibujo',text:'Cancelar'});
        Object.assign(L.drawLocal.draw.toolbar.finish, {title:'Terminar dibujo',text:'Terminar'});
        Object.assign(L.drawLocal.draw.toolbar.undo, {title:'Eliminar último vértice',text:'Deshacer'});
        L.drawLocal.draw.handlers.marker.tooltip.start='Haga clic para colocar un punto';
        L.drawLocal.draw.handlers.rectangle.tooltip.start='Arrastre para dibujar un rectángulo';
        Object.assign(L.drawLocal.draw.handlers.polygon.tooltip, {start:'Haga clic para empezar',cont:'Haga clic para añadir vértices',end:'Pulse el primer vértice para cerrar'});
        Object.assign(L.drawLocal.edit.toolbar.buttons, {edit:'Editar muestras',editDisabled:'No hay muestras para editar',remove:'Eliminar muestras',removeDisabled:'No hay muestras para eliminar'});
        Object.assign(L.drawLocal.edit.toolbar.actions.save, {title:'Guardar cambios',text:'Guardar'});
        Object.assign(L.drawLocal.edit.toolbar.actions.cancel, {title:'Descartar cambios',text:'Cancelar'});
        Object.assign(L.drawLocal.edit.toolbar.actions.clearAll, {title:'Eliminar todas las muestras',text:'Eliminar todas'});
        Object.assign(L.drawLocal.edit.handlers.edit.tooltip, {text:'Arrastre vértices o puntos para editar',subtext:'Pulse Cancelar para descartar cambios'});
        L.drawLocal.edit.handlers.remove.tooltip.text='Pulse una muestra para eliminarla';
        {% endmacro %}
        """)
        locale.add_to(canvas)
        draw = Draw(feature_group=drawn, show_geometry_on_click=False,
                    draw_options={"polygon": {"allowIntersection": False}, "rectangle": {}, "marker": {},
                                  "polyline": False, "circle": False, "circlemarker": False},
                    edit_options={"edit": {}, "remove": {}}).add_to(canvas)
        controls = MacroElement()
        controls.callback = self.callback_id
        controls.colab = output is not None
        controls.save_url = save_url
        controls.sample_files = self.available_sample_files("all")
        controls.active_samples = self.samples_path.name
        controls.active_version = int(re.search(r"-v([1-9]\d*)\.geojson$", self.samples_path.name).group(1))
        controls.export_base = "samples_" + _rf_tag(config)
        controls.models = self.available_models("all")
        controls.region_id = str(config.region_id)
        controls.model_base = "model_" + _rf_tag(config)
        controls.next_model_version = self.catalog()["next_version"]
        controls.next_model_versions = self.catalog()["next_versions"]
        controls.algorithms = FIRE_CLASSIFIERS
        controls.classify_enabled = classify_handler is not None
        controls.initial = {"type": "FeatureCollection", "features": self.features}
        controls.drawn = drawn.get_name()
        controls.mosaic_layer = mosaic_layer.get_name()
        controls.draw_control = draw.get_name()
        controls._template = Template(r"""
        {% macro script(this, kwargs) %}
        (function() {
          const map = {{this._parent.get_name()}}, group = {{this.drawn}};
          // Los vértices de Leaflet.draw deben quedar por encima de los polígonos.
          map.getPane('markerPane').style.zIndex = '650';
          const callback = {{this.callback|tojson}}, inColab = {{this.colab|tojson}};
          const invoke = async (kind, args) => {
            const result = await google.colab.kernel.invokeFunction(callback + '.' + kind, args, {});
            return result.data['application/json'];
          };
          const style = f => ({color: f.properties.class_id === 1 ? '#ff3333' : '#33aa55',
                              fillOpacity: .25, weight: 2, pane: 'rf-samples'});
          function attach(layer) {
            const feature = layer.feature;
            feature.properties = feature.properties || {};
            feature.properties.sample_id = String(feature.properties.sample_id || (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random()));
            feature.properties.class_name = feature.properties.class_id === 1 ? 'Fuego' : 'No fuego';
            if (layer.setStyle) layer.setStyle(style(feature));
            layer.bindTooltip(feature.properties.class_name);
            group.addLayer(layer);
          }
          function populate(data) {
            group.clearLayers();
            L.geoJSON(data, {style:style, pane:'rf-samples',
              pointToLayer:(f,latlng) => L.marker(latlng,{pane:'rf-samples'})
            }).eachLayer(attach);
          }
          const panel = L.control({position:'topright'});
          panel.onAdd = () => {
            const div = L.DomUtil.create('div');
            div.className='fuego-rf-panel';
            div.style.cssText = 'background:#fff;color:#253040;padding:14px;border-radius:10px;box-shadow:0 3px 15px #0004;font:13px Arial;width:335px;max-height:520px;overflow:auto';
            div.innerHTML = '<style>.fuego-rf-panel button{border:1px solid #cbd5e1;border-radius:5px;background:#f8fafc;padding:6px 8px;cursor:pointer;font:inherit;margin:2px 0}.fuego-rf-panel select,.fuego-rf-panel input[type=number]{border:1px solid #cbd5e1;border-radius:4px;padding:4px;font:inherit}.fuego-rf-panel button:disabled,.fuego-rf-panel select:disabled{opacity:.55;cursor:default}.fuego-rf-panel code{color:#526275;font-size:11px;background:#f5f7f9;border-radius:4px;padding:5px}</style>' +
              '<b style="font-size:15px">Monitor de fuego · Clasificación</b><div style="color:#657080;margin:5px 0 12px">Región '+{{this.region_id|tojson}}+' · '+{{this.export_base|tojson}}.split('_').slice(3).join(' a ')+'</div>' +
              '<details open><summary style="font-weight:bold;cursor:pointer">1. Muestras</summary>' +
              '<div class="rf-canvas-counts" style="margin:8px 0" aria-live="polite"></div>' +
              '<label style="display:block;margin-top:8px">Mostrar <select class="rf-sample-scope"><option value="region">Región actual</option><option value="all">Todas las regiones</option></select></label>' +
              '<label style="display:block;margin:8px 0">Archivo de muestras:<select class="rf-sample-file" style="display:block;width:100%;margin-top:4px"></select></label>' +
              '<button type="button" class="rf-load">Cargar seleccionadas</button> <button type="button" class="rf-save">Guardar muestras</button>' +
              '<div style="display:flex;gap:10px;margin:10px 0"><label>Clase:<select class="rf-class"><option value="0">No fuego · 0</option><option value="1">Fuego · 1</option></select></label>' +
              '<label>Versión:<input class="rf-version" type="number" min="1" step="1" value="1" style="width:50px"></label></div>' +
              '<small>Edite o elimine con la barra del mapa; termine con Guardar o Cancelar.</small>' +
              '<code class="rf-export-preview" style="display:block;overflow-wrap:anywhere;margin:8px 0"></code>' +
              '<button type="button" class="rf-export">Exportar GeoJSON</button>' +
              '<div class="rf-status" style="margin:8px 0;color:#536173"></div></details>' +
              '<hr style="border:0;border-top:1px solid #ddd;margin:12px 0">' +
              '<details><summary style="font-weight:bold;cursor:pointer">2. Modelo y clasificación</summary>' +
              '<label style="display:block;margin:8px 0">Acción:<select class="rf-action" style="display:block;width:100%;margin-top:4px"><option value="reuse">Aplicar modelo guardado</option><option value="retrain">Reentrenar seleccionado</option><option value="new">Crear modelo nuevo</option></select></label>' +
              '<label style="display:block;margin:8px 0">Algoritmo:<select class="rf-algorithm" style="display:block;width:100%;margin-top:4px"></select></label>' +
              '<label>Mostrar <select class="rf-model-scope"><option value="region">Región actual</option><option value="all">Todas las regiones</option></select></label>' +
              '<label style="display:block;margin:8px 0">Modelo:<select class="rf-model" style="display:block;width:100%;margin-top:4px"></select></label>' +
              '<div class="rf-training-inputs" style="margin:10px 0"><label>Mostrar muestras <select class="rf-training-scope"><option value="region">Región actual</option><option value="all">Todas las regiones</option></select></label>' +
              '<label style="display:block;margin-top:8px">Muestras para entrenar:<select class="rf-training-samples" style="display:block;width:100%;margin-top:4px"></select></label>' +
              '<div class="rf-training-counts" style="margin-top:8px" aria-live="polite"></div></div>' +
              '<label>Nueva versión: <input class="rf-model-version" type="number" min="1" step="1" value="1" style="width:60px"></label>' +
              '<code class="rf-model-preview" style="display:block;overflow-wrap:anywhere;margin:8px 0"></code>' +
              '<div class="rf-action-hint" style="color:#536173;margin-bottom:8px"></div>' +
              '<button type="button" class="rf-classify" style="background:#174f38;color:white;border:0;border-radius:5px;padding:9px 14px;width:100%">Clasificar</button>' +
              '<label style="display:block;margin-top:8px"><input type="checkbox" class="rf-show" checked disabled> Mostrar fuego</label>' +
              '<div class="rf-job-status" style="margin:8px 0"></div><b class="rf-legend" style="color:#ff261a;display:none">■ Fuego (1)</b>' +
              '<table class="rf-runs" style="display:none;width:100%;margin-top:10px;text-align:left;font-size:11px"><thead><tr><th>Modelo</th><th>Tiempo (s)</th><th>RAM pico (MB)</th></tr></thead><tbody></tbody></table></details>';
            L.DomEvent.disableClickPropagation(div); L.DomEvent.disableScrollPropagation(div); return div;
          };
          panel.addTo(map);
          const panelNode = panel.getContainer(), selector = panelNode.querySelector('.rf-class');
          panelNode.querySelectorAll('details').forEach(detail=>detail.addEventListener('toggle',()=>{
            if(detail.open) panelNode.querySelectorAll('details').forEach(other=>{if(other!==detail) other.open=false;});
          }));
          const status = panelNode.querySelector('.rf-status');
          const sampleFiles = panelNode.querySelector('.rf-sample-file');
          const versionInput = panelNode.querySelector('.rf-version');
          versionInput.value=String({{this.active_version|tojson}});
          let queue = Promise.resolve(), revision = 0, lastSaveOK = true, Raster, rfLayer, nativeEditing = false, jobRunning=false, sampleCatalog=[], modelCatalog=[], countRevision=0, nextVersions={{this.next_model_versions|tojson}};
          const resultLayers=new Map();
          function countText(counts) {
            return 'Muestras · Fuego: '+counts.fire+' · No fuego: '+counts.no_fire;
          }
          function updateCanvasCounts() {
            const counts={fire:0,no_fire:0};
            group.eachLayer(layer=>{
              const value=layer.feature?.properties?.class_id;
              if(value===1) counts.fire++; else if(value===0) counts.no_fire++;
            });
            panelNode.querySelector('.rf-canvas-counts').textContent=countText(counts);
          }
          group.on('layeradd layerremove',updateCanvasCounts);
          updateCanvasCounts();
          async function updateTrainingCounts() {
            const request=++countRevision, filename=panelNode.querySelector('.rf-training-samples').value;
            const label=panelNode.querySelector('.rf-training-counts');
            if(!filename) {label.textContent='Seleccione un archivo para ver sus muestras.';return;}
            label.textContent='Contando muestras…';
            try {
              const counts=await jobRequest('sample_counts',[filename]);
              if(request===countRevision) label.textContent=countText(counts);
            } catch(error) {if(request===countRevision) label.textContent='No se pudieron contar las muestras: '+error.message;}
          }
          function updateTrainingSamples() {
            const select=panelNode.querySelector('.rf-training-samples'), previous=select.value;
            const files=sampleCatalog.filter(name=>panelNode.querySelector('.rf-training-scope').value==='all' || name.match(/^samples_region_([^_]+)_/)[1]==={{this.region_id|tojson}});
            select.replaceChildren();
            const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=files.length ? 'Seleccione un archivo de muestras' : 'Sin archivos guardados';select.appendChild(placeholder);
            files.forEach(name=>{const option=document.createElement('option');option.value=name;option.textContent=name;select.appendChild(option);});
            if(files.includes(previous)) select.value=previous;
            updateTrainingCounts();
          }
          function updateSampleFiles(files, selected) {
            sampleCatalog=files;
            files=files.filter(name=>panelNode.querySelector('.rf-sample-scope').value==='all' || name.match(/^samples_region_([^_]+)_/)[1]==={{this.region_id|tojson}});
            const previous=selected || sampleFiles.value;
            sampleFiles.replaceChildren();
            files.forEach(name => {const option=document.createElement('option'); option.value=name; option.textContent=name; sampleFiles.appendChild(option);});
            if(!files.length) {const option=document.createElement('option');option.value='';option.textContent='Sin archivos guardados';sampleFiles.appendChild(option);}
            if(files.includes(previous)) sampleFiles.value=previous;
            updateTrainingSamples();
          }
          updateSampleFiles({{this.sample_files|tojson}},{{this.active_samples|tojson}});
          function exportName() {
            return {{this.export_base|tojson}} + '-v' + versionInput.value + '.geojson';
          }
          function updateExportPreview() {
            const valid=/^[1-9]\d*$/.test(versionInput.value);
            panelNode.querySelector('.rf-export-preview').textContent=valid ? exportName() : 'Indique una versión entera mayor que cero';
            panelNode.querySelector('.rf-export').disabled=!valid || nativeEditing;
          }
          versionInput.oninput=updateExportPreview;
          updateExportPreview();
          populate({{this.initial|tojson}});
          status.textContent = 'Cargadas: ' + group.getLayers().length + ' muestras guardadas. Puede editarlas.';
          function save() {
            const data = group.toGeoJSON(), current = ++revision;
            const version=Number(versionInput.value);
            if(!/^[1-9]\d*$/.test(versionInput.value)) {
              lastSaveOK=false;status.textContent='Indique una versión entera mayor que cero.';return;
            }
            lastSaveOK = false;
            status.textContent = 'Guardando…';
            queue = queue.catch(() => {}).then(async () => {
              let response;
              if (inColab) response = await invoke('save', [data,version]);
              else {
                const request = await fetch({{this.save_url|tojson}}, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'save',data:data,version:version})});
                if (!request.ok) throw Error('No se guardaron las muestras');
                response = await request.json();
              }
              lastSaveOK = true;
              updateSampleFiles(response.files || [],response.filename);updateModelAction();
              if (current === revision) status.textContent = 'Guardado: ' + response.count + ' muestras. Puede clasificar.';
            }).catch(error => {status.textContent = 'Error al guardar; pulse Guardar muestras para reintentar.'; console.error(error);});
          }
          selector.onchange = () => {{this.draw_control}}.setDrawingOptions({
            polygon:{shapeOptions:{color:selector.value === '1' ? '#ff3333' : '#33aa55',pane:'rf-samples'}},
            rectangle:{shapeOptions:{color:selector.value === '1' ? '#ff3333' : '#33aa55',pane:'rf-samples'}}});
          selector.onchange();
          map.on('draw:created', e => {
            const label = Number(selector.value), feature = e.layer.toGeoJSON();
            feature.properties = {sample_id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random(),
                                  class_id:label, class_name:label ? 'Fuego' : 'No fuego'};
            e.layer.feature = feature;
            if (e.layer.setStyle) e.layer.setStyle(style(feature));
            attach(e.layer); save();
          });
          function setNativeEditing(active) {
            nativeEditing=active;
            ['.rf-save','.rf-load','.rf-export','.rf-classify','.rf-sample-scope','.rf-model-scope','.rf-action','.rf-training-scope','.rf-training-samples','.rf-algorithm'].forEach(name => {panelNode.querySelector(name).disabled=active;});
            sampleFiles.disabled=active;
            if(!active) {panelNode.querySelector('.rf-classify').disabled=!{{this.classify_enabled|tojson}};updateExportPreview();updateModelAction();}
          }
          map.on('draw:editstart draw:deletestart', () => {
            setNativeEditing(true);status.textContent='Use Guardar o Cancelar en la barra de Folium para terminar.';
          });
          map.on('draw:editstop draw:deletestop', () => setNativeEditing(false));
          map.on('draw:edited draw:deleted', () => {nativeEditing=false;save();});
          panelNode.querySelector('.rf-save').onclick = save;
          panelNode.querySelector('.rf-load').onclick = () => {
            const filename=sampleFiles.value;
            if(!filename) {status.textContent='No hay archivos de muestras guardados.';return;}
            const current = ++revision;
            status.textContent = 'Cargando muestras guardadas…';
            queue = queue.catch(() => {}).then(async () => {
              let data;
              if (inColab) data = await invoke('load', [filename]);
              else {
                const response = await fetch({{this.save_url|tojson}}, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'load',filename:filename})});
                if (!response.ok) throw Error('No se pudieron cargar las muestras');
                data = await response.json();
              }
              if (current === revision) {
                populate(data);
                versionInput.value=data.workspace ? data.workspace.version : filename.match(/-v([1-9]\d*)\.geojson$/)[1];
                if(data.workspace) updateSampleFiles([...new Set([...sampleCatalog,data.workspace.filename])],data.workspace.filename);
                updateExportPreview();
                status.textContent = 'Cargadas: ' + data.features.length + ' muestras. Guardado: ' + (data.workspace ? data.workspace.filename : filename);
                refreshCatalog();
              }
            }).catch(error => {status.textContent='Error al cargar: '+error.message; console.error(error);});
          };
          panelNode.querySelector('.rf-export').onclick = async () => {
            const button=panelNode.querySelector('.rf-export');button.disabled=true;
            try {
              if(!/^[1-9]\d*$/.test(versionInput.value)) throw Error('Versión no válida');
              save();await queue;if(!lastSaveOK) throw Error('Guarde las muestras antes de exportar');
              let response;
              if(inColab) response=await invoke('export',[Number(versionInput.value)]);
              else {
                const request=await fetch({{this.save_url|tojson}},{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'export',version:Number(versionInput.value)})});
                if(!request.ok) throw Error('No se pudo exportar');response=await request.json();
              }
              updateSampleFiles(response.files,response.filename);
              const url=URL.createObjectURL(new Blob([JSON.stringify(response.data)],{type:'application/geo+json'}));
              const a=document.createElement('a');a.href=url;a.download=response.filename;a.click();
              setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='Exportado: '+response.filename;
            } catch(error) {status.textContent=error.message;console.error(error);}
            finally {updateExportPreview();}
          };
          const modelSelector = panelNode.querySelector('.rf-model');
          const classifyButton = panelNode.querySelector('.rf-classify');
          const jobStatus = panelNode.querySelector('.rf-job-status');
          const showFire = panelNode.querySelector('.rf-show');
          function updateModels(models, selected) {
            modelCatalog=models;
            models=models.filter(name=>panelNode.querySelector('.rf-model-scope').value==='all' || name.match(/^model_region_([^_]+)_/)[1]==={{this.region_id|tojson}});
            const previous = selected || modelSelector.value;
            modelSelector.replaceChildren();
            models.forEach(name => {const option=document.createElement('option'); option.value=name; option.textContent=name; modelSelector.appendChild(option);});
            if(!models.length) {const option=document.createElement('option'); option.value=''; option.textContent='Sin modelos guardados'; modelSelector.appendChild(option);}
            if(models.includes(previous)) modelSelector.value=previous;
          }
          const algorithms={{this.algorithms|tojson}}, algorithmSelector=panelNode.querySelector('.rf-algorithm');
          Object.entries(algorithms).forEach(([key,name])=>{const option=document.createElement('option');option.value=key;option.textContent=name;algorithmSelector.appendChild(option);});
          algorithmSelector.value='random_forest';
          function modelAlgorithm(name) {
            const base=name.split('-v')[0];
            return Object.keys(algorithms).find(key=>base.endsWith('_'+key)) || 'random_forest';
          }
          function modelBase(algorithm) {
            return {{this.model_base|tojson}}+(algorithm==='random_forest' ? '' : '_'+algorithm);
          }
          const actionSelector=panelNode.querySelector('.rf-action');
          const modelVersion=panelNode.querySelector('.rf-model-version');
          modelVersion.value=String({{this.next_model_version|tojson}});
          function updateModelAction() {
            const mode=actionSelector.value, isNew=mode==='new';
            if(!isNew) algorithmSelector.value=modelAlgorithm(modelSelector.value);
            algorithmSelector.disabled=!isNew || nativeEditing || jobRunning;
            actionSelector.disabled=nativeEditing || jobRunning;
            const base=modelBase(algorithmSelector.value);
            panelNode.querySelector('.rf-training-inputs').hidden=mode==='reuse';
            panelNode.querySelector('.rf-training-samples').disabled=mode==='reuse' || nativeEditing || jobRunning;
            panelNode.querySelector('.rf-training-scope').disabled=mode==='reuse' || nativeEditing || jobRunning;
            const hasSamples=!!panelNode.querySelector('.rf-training-samples').value;
            modelSelector.disabled=isNew || nativeEditing || jobRunning;
            modelVersion.disabled=!isNew || nativeEditing || jobRunning;
            panelNode.querySelector('.rf-model-preview').textContent=isNew ? base+'-v'+modelVersion.value+'.joblib' : modelSelector.value;
            const compatibleName=modelSelector.value.startsWith(base+'-v');
            const validVersion=/^[1-9]\d*$/.test(modelVersion.value);
            const existingVersion=modelCatalog.includes(base+'-v'+modelVersion.value+'.joblib');
            panelNode.querySelector('.rf-action-hint').textContent=isNew ? 'Crea otra versión y conserva los modelos anteriores.' : mode==='retrain' ? 'Reemplaza el modelo seleccionado usando el archivo de muestras elegido.' : 'Aplica el modelo sin modificarlo; las bandas deben coincidir.';
            if(mode==='retrain' && !compatibleName) panelNode.querySelector('.rf-action-hint').textContent='Para otra región o mes, elija Crear modelo nuevo.';
            if(isNew && existingVersion) panelNode.querySelector('.rf-action-hint').textContent='Esa versión ya existe. Elija otro número para crear un modelo nuevo.';
            classifyButton.textContent=mode==='reuse' ? 'Clasificar' : 'Entrenar y clasificar';
            classifyButton.disabled=!{{this.classify_enabled|tojson}} || nativeEditing || jobRunning || (mode!=='reuse' && !hasSamples) || (isNew && (!validVersion || existingVersion)) || (!isNew && (!modelSelector.value || (mode==='retrain' && !compatibleName)));
          }
          updateModels({{this.models|tojson}});
          if(!modelSelector.value) actionSelector.value='new';
          algorithmSelector.onchange=()=>{modelVersion.value=String(nextVersions[algorithmSelector.value] || 1);updateModelAction();};
          actionSelector.onchange=()=>{if(actionSelector.value==='new') modelVersion.value=String(nextVersions[algorithmSelector.value] || 1);updateModelAction();};modelSelector.onchange=updateModelAction;modelVersion.oninput=updateModelAction;
          updateModelAction();
          async function refreshCatalog() {
            try {
              const catalog=await jobRequest('catalog');
              nextVersions=catalog.next_versions;updateSampleFiles(catalog.samples);updateModels(catalog.models);updateModelAction();
            } catch(error) {status.textContent='No se pudo actualizar la lista: '+error.message;}
          }
          panelNode.querySelector('.rf-sample-scope').onchange=()=>{updateSampleFiles(sampleCatalog);refreshCatalog();};
          panelNode.querySelector('.rf-training-samples').onchange=()=>{updateModelAction();updateTrainingCounts();};
          panelNode.querySelector('.rf-training-scope').onchange=()=>{updateTrainingSamples();updateModelAction();refreshCatalog();};
          panelNode.querySelector('.rf-model-scope').onchange=()=>{updateModels(modelCatalog);updateModelAction();refreshCatalog();};
          async function jobRequest(kind, args) {
            if(inColab) return invoke(kind, args || []);
            const data=kind === 'start' ? {action:kind,model:args[0],mode:args[1],version:args[2],samples:args[3],algorithm:args[4]} : kind==='sample_counts' ? {action:kind,filename:args[0]} : {action:kind};
            const response=await fetch({{this.save_url|tojson}}, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
            if(!response.ok) throw Error('No se pudo comunicar con el proceso RF');
            return response.json();
          }
          showFire.onchange = () => {
            if(!rfLayer) return;
            if(showFire.checked) rfLayer.addTo(map); else map.removeLayer(rfLayer);
          };
          classifyButton.onclick = async () => {
            jobRunning=true;updateModelAction();
            try {
              const mode=actionSelector.value;
              const selectedSamples=panelNode.querySelector('.rf-training-samples').value;
              if(mode!=='reuse' && !selectedSamples) throw Error('Seleccione un archivo de muestras para entrenar.');
              await queue;
              jobStatus.textContent='Iniciando clasificación…';
              let response=await jobRequest('start',[modelSelector.value,mode,Number(modelVersion.value),mode==='reuse' ? null : selectedSamples,algorithmSelector.value]);
              const started=Date.now();
              while(response.state === 'running') {
                jobStatus.textContent='Clasificando… ' + Math.round((Date.now()-started)/1000) + ' s';
                await new Promise(resolve => setTimeout(resolve,1000));
                response=await jobRequest('status');
              }
              if(response.state !== 'done') throw Error(response.error || 'No se completó la clasificación');
              const previous=resultLayers.get(response.model);
              if(previous) {map.removeLayer(previous);{{this.layer_control}}.removeLayer(previous);}
              rfLayer=inColab ? new Raster({pane:'rf-classes',opacity:.7,maxZoom:24,keepBuffer:0,updateWhenIdle:true,callbackKind:'result_tile',resultId:response.result_id})
                             : L.tileLayer(response.result_url,{pane:'rf-classes',opacity:.7,maxZoom:24});
              resultLayers.set(response.model,rfLayer);
              const table=panelNode.querySelector('.rf-runs');table.style.display='table';
              let row=Array.from(table.tBodies[0].rows).find(row=>row.dataset.model===response.model);
              if(!row) {row=table.tBodies[0].insertRow();row.dataset.model=response.model;}
              row.replaceChildren();row.title=response.model;
              [response.algorithm_name+' · v'+response.model.match(/-v([0-9]+)\.joblib$/)[1],response.seconds.toFixed(1),response.ram_mb.toFixed(0)].forEach(value=>{row.insertCell().textContent=value;});
              {{this.layer_control}}.addOverlay(rfLayer,'Fuego · '+response.algorithm_name+' · '+response.model);
              nextVersions=response.next_versions;
              showFire.disabled=false; showFire.checked=true; rfLayer.addTo(map);
              panelNode.querySelector('.rf-legend').style.display='inline';
              updateModels(response.models,response.model);
              modelVersion.value=String(nextVersions[response.algorithm] || 1);actionSelector.value='reuse';updateModelAction();
              jobStatus.textContent='Terminado: ' + response.seconds.toFixed(1) + ' s · RAM pico: ' + response.ram_mb.toFixed(0) + ' MB';
            } catch(error) {jobStatus.textContent=error.message; console.error(error);}
            finally {jobRunning=false;updateModelAction();}
          };
          if (inColab) {
            // Una petición a la vez y solo teselas aún visibles: no acumula lecturas de ráster.
            const pending = []; let busy = false;
            async function pump() {
              if (busy) return; busy = true;
              while (pending.length) {
                const task = pending.shift();
                if (!task.tile.isConnected) {task.done(null,task.tile); continue;}
                try {
                  const args=[task.coords.z,task.coords.x,task.coords.y];
                  if(task.kind==='result_tile') args.push(task.resultId);
                  const response = await invoke(task.kind,args);
                  task.tile.onload = () => task.done(null,task.tile);
                  task.tile.onerror = () => task.done(Error('No se pudo mostrar la tesela'),task.tile);
                  task.tile.src = 'data:image/png;base64,' + response.png;
                } catch(error) {status.textContent='No se pudo leer el mosaico. Compruebe la sesión.'; task.done(error,task.tile);}
              }
              busy = false;
            }
            Raster = L.GridLayer.extend({createTile:function(coords,done) {
              const img = document.createElement('img'); img.width=256; img.height=256; img.alt='';
              pending.push({tile:img,coords:coords,done:done,kind:this.options.callbackKind || 'tile',resultId:this.options.resultId}); setTimeout(pump,0); return img;
            }});
            const raster = new Raster({pane:'scene-rasters',maxZoom:24,keepBuffer:0,updateWhenIdle:true});
            raster.addTo({{this.mosaic_layer}});
          }
          map.getContainer().setAttribute('lang','es');
        })();
        {% endmacro %}
        """)
        controls.add_to(canvas)
        layer_control = folium.LayerControl(position="bottomright").add_to(canvas)
        controls.layer_control = layer_control.get_name()
        canvas.fit_bounds(bounds)
        self.html_path = composite.path.parent / "rf" / "training_folium_map.html"
        canvas.save(str(self.html_path))
        print("Canvas Folium · bandas:", vis["bands"])
        print("Espere «Guardado» antes de entrenar. Muestras:", self.samples_path)
        if self.archive_folder is not None:
            print("Muestras en Drive:", self.archive_folder / self.samples_path.name)

    def save_samples(self, data, version=None):
        if version is not None:
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("La versión debe ser un entero mayor que cero.")
            destination = self.samples_path.parent / f"samples_{_rf_tag(self.config)}-v{version}.geojson"
        else:
            destination = self.samples_path
        features = data.get("features", [])
        if data.get("type") != "FeatureCollection" or len(features) > 10000:
            raise ValueError("Colección de muestras no válida.")
        for feature in features:
            properties = feature.get("properties") or {}
            if properties.get("class_id") not in (0, 1):
                raise ValueError("Las clases admitidas son 0 y 1.")
            geometry = shape(feature["geometry"])
            if geometry.geom_type not in {"Point", "Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
                raise ValueError("Use puntos o polígonos válidos.")
        temporary = destination.with_suffix(".tmp.geojson")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(destination)
        if self.archive_folder is not None:
            _atomic_copy(destination, self.archive_folder / destination.name)
        self.samples_path = destination
        _write_rf_sample_selection(destination.parent, destination.name, self.archive_folder)
        self.features = json.loads(json.dumps(features))
        return {"count": len(features), "files": self.available_sample_files("all"), "filename": destination.name,
                "version": int(re.search(r"-v([1-9]\d*)\.geojson$", destination.name).group(1))}

    def samples(self):
        if not self.samples_path.is_file():
            return {"type": "FeatureCollection", "features": []}
        return json.loads(self.samples_path.read_text())

    def available_sample_files(self, scope="region"):
        return [p.name for p in _catalog_files("samples", [self.samples_path.parent, self.archive_folder],
                                              None if scope == "all" else self.config.region_id)]

    def load_samples(self, filename=None):
        if filename:
            paths = {p.name: p for p in _catalog_files("samples", [self.samples_path.parent, self.archive_folder])}
            if filename not in paths:
                raise ValueError("Seleccione un archivo de muestras disponible para esta región y período.")
            data = json.loads(paths[filename].read_text())
            version = int(re.search(r"-v([1-9]\d*)\.geojson$", filename).group(1))
            if not filename.startswith(f"samples_{_rf_tag(self.config)}-v"):
                version = _next_catalog_version("samples", _rf_tag(self.config), [self.samples_path.parent, self.archive_folder])
            saved = self.save_samples(data, version)
            return {**data, "workspace": {"filename": saved["filename"], "version": saved["version"]}}
        if self.archive_folder is not None:
            stored = self.archive_folder / self.samples_path.name
            if stored.is_file():
                _atomic_copy(stored, self.samples_path)
        data = self.samples()
        self.features = json.loads(json.dumps(data["features"]))
        return data

    def export_samples(self, version=1):
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("La versión debe ser un entero mayor que cero.")
        data = self.samples()
        response = self.save_samples(data, version)
        return {**response, "data": data}

    def _sample_request(self, data):
        action = data.get("action")
        if action == "load":
            return self.load_samples(data.get("filename"))
        if action == "export":
            return self.export_samples(data.get("version", 1))
        if action == "save":
            return self.save_samples(data["data"], data.get("version"))
        if action == "start":
            return self.start_classification(data.get("model", ""), data.get("mode", bool(data.get("retrain"))), data.get("version"), data.get("samples"), data.get("algorithm", "random_forest"))
        if action == "sample_counts":
            return self.sample_counts(data.get("filename"))
        if action == "catalog":
            return self.catalog()
        if action == "status":
            return self.classification_status()
        return self.save_samples(data)

    def sample_counts(self, filename):
        """Cuenta geometrías del archivo elegido, sin cambiar las muestras del canvas."""
        paths = {p.name: p for p in _catalog_files("samples", [self.samples_path.parent, self.archive_folder])}
        if filename not in paths:
            raise ValueError("Seleccione un archivo de muestras disponible.")
        features = json.loads(paths[filename].read_text())["features"]
        return {"fire": sum(f.get("properties", {}).get("class_id") == 1 for f in features),
                "no_fire": sum(f.get("properties", {}).get("class_id") == 0 for f in features)}

    def available_models(self, scope="region"):
        return [p.name for p in _catalog_files("model", [self.models_folder],
                                              None if scope == "all" else self.config.region_id)]

    def catalog(self):
        return {"samples": self.available_sample_files("all"), "models": self.available_models("all"),
                "next_version": _next_catalog_version("model", _rf_tag(self.config), [self.models_folder]),
                "next_versions": {key: _next_catalog_version("model", _model_tag(self.config, key), [self.models_folder])
                                  for key in FIRE_CLASSIFIERS}}

    def start_classification(self, model_name="", retrain=False, version=None, sample_name=None, algorithm="random_forest"):
        mode = retrain if isinstance(retrain, str) else ("retrain" if retrain else "reuse")
        with self.job_lock:
            if self.job["state"] == "running":
                return dict(self.job)
            if self.classify_handler is None:
                return {"state": "error", "error": "Abra el canvas desde workflow.training_canvas para clasificar."}
            if algorithm not in FIRE_CLASSIFIERS:
                return {"state": "error", "error": "Seleccione un algoritmo de clasificación disponible."}
            if mode not in {"reuse", "retrain", "new"}:
                return {"state": "error", "error": "Elija una acción RF válida."}
            if mode == "reuse" and model_name not in self.available_models("all"):
                return {"state": "error", "error": "Seleccione un modelo o elija Crear modelo nuevo."}
            if mode == "retrain" and model_name and model_name not in self.available_models("all"):
                return {"state": "error", "error": "El modelo seleccionado ya no está disponible."}
            if self.advanced_actions and mode != "reuse" and sample_name not in self.available_sample_files("all"):
                return {"state": "error", "error": "Seleccione un archivo de muestras disponible para entrenar."}
            self.job = {"state": "running"}

        def run():
            try:
                result = (self.classify_handler(model_name, mode != "reuse", mode, version, sample_name, algorithm) if self.advanced_actions
                          else self.classify_handler(model_name, mode != "reuse"))
                url = ""
                if not self.in_colab:
                    spec = {"path": str(result.path.resolve()), "indexes": [1], "mode": "fire_only", "min": 0, "max": 1}
                    url = _NATIVE_TILE_SERVICE.register(spec)
                metrics = json.loads(result.metrics_path.read_text())
                with self.job_lock:
                    self.classification, self.result_url = result, url
                    result_id = uuid4().hex
                    self.results[result_id] = result
                    self.job = {"state": "done", "seconds": metrics["tiempo_total_s"],
                                "ram_mb": metrics["ram_pico_mb"], "model": result.model_path.name,
                                "algorithm": metrics.get("algoritmo", "random_forest"),
                                "algorithm_name": FIRE_CLASSIFIERS[metrics.get("algoritmo", "random_forest")],
                                "result_id": result_id}
            except Exception as error:
                with self.job_lock:
                    self.job = {"state": "error", "error": str(error)}
        self.job_thread = Thread(target=run, daemon=True)
        self.job_thread.start()
        return {"state": "running"}

    def classification_status(self):
        with self.job_lock:
            return {**self.job, **self.catalog(), "result_url": self.result_url}

    def result_tile(self, z, x, y, result_id=None):
        import base64
        with self.job_lock:
            result = self.results.get(result_id) if result_id is not None else self.classification
        if result is None:
            raise ValueError("Todavía no hay una clasificación terminada.")
        spec = {"path": str(result.path.resolve()), "indexes": [1], "mode": "fire_only", "min": 0, "max": 1}
        png = _render_native_tile(spec, int(z), int(x), int(y))
        return {"png": base64.b64encode(png).decode("ascii")}

    def show(self):
        from IPython.display import HTML, display
        display(HTML(self.map.get_root().render()) if self.in_colab else self.map)


def create_rf_training_folium(config: PipelineConfig, composite: FireCompositeResult, **kwargs):
    """Construye el paso 7 con Folium, sin importar geemap ni ipyleaflet."""
    return RFTrainingFoliumCanvas(config, composite, **kwargs)


class RFTrainingCanvas:
    """Muestras locales etiquetadas al dibujar; se guardan automáticamente como GeoJSON."""
    def __init__(self, config: PipelineConfig, composite: FireCompositeResult, *, max_pixels: int = 1536):
        import ipywidgets as widgets
        from ipyleaflet import DrawControl, WidgetControl, GeoJSON
        self.config, self.composite = config, composite
        self.samples_path = _prepare_rf_workspace(composite, _rf_tag(config))["samples"]
        self.samples_path.parent.mkdir(parents=True, exist_ok=True)
        self.features = json.loads(self.samples_path.read_text())["features"] if self.samples_path.is_file() else []
        self.map = _local_mosaic_geemap(config, composite, max_pixels)
        self.class_selector = widgets.Dropdown(options=[("No fuego · 0", 0), ("Fuego · 1", 1)], description="Clase:")
        self.sample_selector = widgets.Dropdown(description="Muestra:", layout=widgets.Layout(width="300px"))
        self.status = widgets.HTML()
        delete = widgets.Button(description="Eliminar muestra", icon="trash")
        delete.on_click(self._delete_selected)
        self.draw = DrawControl(polygon={"shapeOptions": {"color": "#33aa55"}},
                                rectangle={"shapeOptions": {"color": "#33aa55"}}, marker={},
                                circle={}, circlemarker={}, polyline={}, edit=False, remove=False)
        # Marcadores de puntos; polígonos y rectángulos aportan varios píxeles.
        self.draw.marker = {"repeatMode": False}
        self.draw.on_draw(self._on_draw)
        self.class_selector.observe(self._set_color, names="value")
        self.sample_layer = GeoJSON(data={"type": "FeatureCollection", "features": []}, name="Muestras RF",
                                    pane="monitor-vectors", style_callback=self._feature_style)
        self.map.add(self.sample_layer)
        self.map.add(self.draw)
        panel = widgets.VBox([widgets.HTML("<b>Muestras RF: elija clase y dibuje</b>"), self.class_selector,
                              self.sample_selector, delete, self.status])
        self.map.add(WidgetControl(widget=panel, position="bottomleft"))
        self._sync()

    @staticmethod
    def _feature_style(feature):
        color = "#ff3333" if feature["properties"]["class_id"] == 1 else "#33aa55"
        return {"color": color, "fillColor": color, "fillOpacity": 0.25, "weight": 2}

    def _set_color(self, _=None):
        color = "#ff3333" if self.class_selector.value == 1 else "#33aa55"
        self.draw.polygon = {"shapeOptions": {"color": color}}
        self.draw.rectangle = {"shapeOptions": {"color": color}}

    def _on_draw(self, _, action, geo_json):
        if action != "created":
            return
        geometry = geo_json.get("geometry")
        if geometry is None or shape(geometry).geom_type not in {"Point", "Polygon", "MultiPolygon"}:
            self.status.value = "Use puntos, polígonos o rectángulos."
            return
        label = int(self.class_selector.value)
        self.features.append({"type": "Feature", "geometry": geometry, "properties": {
            "sample_id": uuid4().hex, "class_id": label, "class_name": "Fuego" if label else "No fuego"}})
        self.draw.clear()
        self._sync()

    def _delete_selected(self, _=None):
        self.features = [f for f in self.features if f["properties"]["sample_id"] != self.sample_selector.value]
        self._sync()

    def _sync(self):
        data = {"type": "FeatureCollection", "features": self.features}
        self.sample_layer.data = json.loads(json.dumps(data))
        previous = self.sample_selector.value
        self.sample_selector.options = [(f"{i} · {f['properties']['class_name']}", f["properties"]["sample_id"])
                                        for i, f in enumerate(self.features, 1)]
        identifiers = [f["properties"]["sample_id"] for f in self.features]
        self.sample_selector.value = previous if previous in identifiers else (identifiers[0] if identifiers else None)
        counts = {label: sum(f["properties"]["class_id"] == label for f in self.features) for label in (0, 1)}
        self.status.value = f"No fuego: {counts[0]} geometrías · Fuego: {counts[1]} geometrías. Guardado automático."
        temporary = self.samples_path.with_suffix(".tmp.geojson")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(self.samples_path)

    def samples(self) -> dict:
        return json.loads(json.dumps({"type": "FeatureCollection", "features": self.features}))

    def show(self):
        """Muestra explícitamente el canvas, también dentro de funciones o celdas de Colab."""
        from IPython.display import display
        display(self.map)


def create_rf_result_map(config: PipelineConfig, composite: FireCompositeResult, classification: RFResult,
                          *, max_pixels: int = 1536):
    import geemap
    from ipyleaflet import WidgetControl
    from ipywidgets import HTML
    canvas = _local_mosaic_geemap(config, composite, max_pixels)
    layer, _ = _native_raster_layer(classification.path, bands=["fire_class"], name="RF · fuego / no fuego",
                                     mode="classes", opacity=0.75)
    canvas.add(layer)
    canvas.add(WidgetControl(widget=HTML('<b style="color:#ff3322">■ Fuego (1)</b> · '
                                         '<b style="color:#26a64d">■ No fuego (0)</b> · Transparente: sin datos'),
                             position="bottomleft"))
    canvas.to_html(str(classification.path.parent / "rf_classification_map.html"))
    return canvas


def _render_native_tile(spec: dict, z: int, x: int, y: int) -> bytes:
    """Lee una ventana acotada antes de reproyectar; nunca entrega el COG entero al warper."""
    from PIL import Image
    from rasterio.warp import reproject
    from rasterio.windows import from_bounds as bounds_window
    if not 0 <= z <= 24 or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise ValueError("Coordenadas de tesela no válidas.")
    radius = 20037508.342789244
    span = 2 * radius / 2**z
    west, north = -radius + x * span, radius - y * span
    transform = from_bounds(west, north - span, west + span, north, 256, 256)
    values = np.full((len(spec["indexes"]), 256, 256), np.nan, dtype="float32")
    with rasterio.Env(GDAL_CACHEMAX=64 * 1024**2, GDAL_NUM_THREADS="1"), rasterio.open(spec["path"]) as src:
        source_bounds = transform_bounds("EPSG:3857", src.crs, west, north - span, west + span, north,
                                         densify_pts=21)
        window = bounds_window(*source_bounds, transform=src.transform)
        # Dos píxeles de margen evitan bordes al transformar entre UTM y Web Mercator.
        col0, row0 = max(0, int(np.floor(window.col_off)) - 2), max(0, int(np.floor(window.row_off)) - 2)
        col1 = min(src.width, int(np.ceil(window.col_off + window.width)) + 2)
        row1 = min(src.height, int(np.ceil(window.row_off + window.height)) + 2)
        if col1 > col0 and row1 > row0:
            window = Window(col0, row0, col1 - col0, row1 - row0)
            # GDAL puede usar las pirámides del COG en esta lectura reducida.
            # Incluso sin pirámides, los arrays de entrada están limitados a 1024².
            height, width = min(1024, int(window.height)), min(1024, int(window.width))
            source_values = src.read(spec["indexes"], window=window,
                                     out_shape=(len(spec["indexes"]), height, width),
                                     resampling=Resampling.nearest, masked=True).astype("float32").filled(np.nan)
            source_transform = src.window_transform(window) * rasterio.Affine.scale(
                window.width / width, window.height / height)
            for index in range(len(spec["indexes"])):
                reproject(source_values[index], values[index], src_transform=source_transform, src_crs=src.crs,
                          dst_transform=transform, dst_crs="EPSG:3857", src_nodata=np.nan, dst_nodata=np.nan,
                          resampling=Resampling.nearest, warp_mem_limit=16, num_threads=1)
    valid = np.isfinite(values).all(axis=0)
    safe = np.nan_to_num(values, nan=0, posinf=0, neginf=0)
    if spec["mode"] in {"classes", "fire_only"}:
        rgba = np.empty((256, 256, 4), dtype="uint8")
        rgba[:, :, :3] = np.where(safe[0, :, :, None] == 1, [255, 38, 26], [38, 166, 77])
        if spec["mode"] == "fire_only":
            valid &= safe[0] == 1
    elif spec["mode"] == "NBR":
        from matplotlib import colormaps
        rgba = (colormaps["RdYlGn"](np.clip((safe[0] + 1) / 2, 0, 1)) * 255).astype("uint8")
    else:
        low, high = spec["min"], spec["max"]
        rgb = np.clip((np.moveaxis(safe, 0, -1) - low) / (high - low), 0, 1)
        rgba = np.empty((256, 256, 4), dtype="uint8")
        rgba[:, :, :3] = np.round(rgb * 255).astype("uint8")
    rgba[:, :, 3] = valid.astype("uint8") * 255
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    return buffer.getvalue()


class _NativeTileService:
    """Servidor local de solo lectura: publica teselas de rásteres registrados por token."""
    transport_version = 4
    def __init__(self):
        self.specs = {}
        self.sample_callbacks = {}
        self.lock = Lock()
        self.slots = BoundedSemaphore(1)
        self._base_url = None
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_POST(self):
                match = re.fullmatch(r"/samples/([a-f0-9]{32})", urlsplit(self.path).path)
                callback = service.sample_callbacks.get(match.group(1)) if match else None
                if callback is None:
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 2 * 1024**2:
                        self.send_error(413)
                        return
                    response = callback(json.loads(self.rfile.read(length)))
                except (ValueError, KeyError, TypeError):
                    self.send_error(400)
                    return
                body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                match = re.fullmatch(r"/tiles/([a-f0-9]{32})/(\d+)/(\d+)/(\d+)\.png", urlsplit(self.path).path)
                if match is None:
                    self.send_error(404)
                    return
                token, z, x, y = match.groups()
                with service.lock:
                    spec = service.specs.get(token)
                if spec is None:
                    self.send_error(404)
                    return
                try:
                    with service.slots:
                        png = _render_native_tile(spec, int(z), int(x), int(y))
                except ValueError:
                    self.send_error(400)
                    return
                except Exception:
                    logging.getLogger(__name__).exception("No se pudo leer una tesela del mosaico local.")
                    self.send_error(500, "No se pudo leer la tesela local")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "private, max-age=300")
                self.end_headers()
                try:
                    self.wfile.write(png)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # El navegador cambió de zoom antes de recibir la tesela.

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        atexit.register(self.close)

    def base_url(self):
        if self._base_url is None:
            port = self.server.server_address[1]
            try:
                import google.colab
            except ImportError:
                self._base_url = f"http://127.0.0.1:{port}"
            else:
                try:
                    import jupyter_loopback
                except ImportError as exc:
                    raise ImportError('Ejecute la instalación de la sección 1: falta jupyter-loopback[comm].') from exc
                # El puente de widgets entrega las teselas desde el kernel; no espera
                # una respuesta síncrona de JavaScript ni expone el puerto a Internet.
                jupyter_loopback.enable_comm_bridge()
                jupyter_loopback.intercept_localhost(port)
                self._base_url = f"http://127.0.0.1:{port}"
        return self._base_url

    def register(self, spec):
        token = uuid4().hex
        with self.lock:
            self.specs[token] = spec
        return self.base_url() + f"/tiles/{token}/{{z}}/{{x}}/{{y}}.png"

    def register_samples(self, callback):
        token = uuid4().hex
        self.sample_callbacks[token] = callback
        return self.base_url() + f"/samples/{token}"

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


# Al recargar el .py en Colab se reutiliza el servidor que ya esté activo.
_NATIVE_TILE_SERVICE = globals().get("_NATIVE_TILE_SERVICE")
if _NATIVE_TILE_SERVICE is not None and getattr(_NATIVE_TILE_SERVICE, "transport_version", None) != 4:
    _NATIVE_TILE_SERVICE.close()
    _NATIVE_TILE_SERVICE = None


def _native_raster_layer(path: Path, *, bands: list[str], name: str, minimum=0.03, maximum=0.4,
                          mode="color", opacity=1):
    from ipyleaflet import TileLayer
    global _NATIVE_TILE_SERVICE
    path = Path(path).resolve()
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError(f"Falta el sistema de referencia de {path.name}.")
        missing = set(bands) - set(src.descriptions)
        if missing:
            raise ValueError(f"Faltan bandas en {path.name}: {sorted(missing)}")
        spec = {"path": str(path), "indexes": [src.descriptions.index(b) + 1 for b in bands],
                "mode": mode, "min": minimum, "max": maximum}
        bounds = _leaflet_bounds(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
        print(f"GeoTIFF local: {path.name}; {src.width} × {src.height} píxeles; "
              f"resolución: {src.res[0]:g} × {src.res[1]:g} {src.crs.linear_units if src.crs.is_projected else 'grados'}.")
    if _NATIVE_TILE_SERVICE is None:
        _NATIVE_TILE_SERVICE = _NativeTileService()
    layer = TileLayer(url=_NATIVE_TILE_SERVICE.register(spec), name=name, pane="scene-rasters",
                       attribution="Mosaico local · resolución original", max_zoom=24, max_native_zoom=24,
                       opacity=opacity)
    return layer, bounds
