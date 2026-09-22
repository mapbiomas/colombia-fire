# A_4_1_tensorflow_feature_maps_extraction.py
# last update: '2025/06/02'
# MapBiomas Fire Classification Algorithms Step A_4_1 Functions for TensorFlow Embedding Extraction
# (Modified version for dynamic selection of the embedding layer)

# INSTALAR E IMPORTAR LIBRARIES
import os
import numpy as np
import tensorflow as tf
import tensorflow.compat.v1 as tf
tf.disable_v2_behavior() # Use only the 1.x compatible version
from osgeo import gdal
import rasterio
from rasterio.mask import mask
import ee
from tqdm import tqdm
import time
from datetime import datetime
import math
from shapely.geometry import shape, box, mapping
from shapely.ops import transform
import pyproj
import shutil
import json
import subprocess
from scipy import ndimage 

# Global variables (assumed or mocked for running the script)
# You must make sure 'log_message', 'bucket_name', 'ee_project' and 'fs' are defined in the environment
def log_message(msg):
    print(f"[LOG] {msg}")

# Utility functions (based on A_3_1)

def fully_connected_layer (input, n_neurons, activation=None, name=None):
    """Creates a fully connected layer."""
    input_size = input.get_shape().as_list()[1]
    W = tf.Variable(tf.truncated_normal([input_size, n_neurons], stddev=1.0 / math.sqrt(float(input_size))), name=f'W_{name}')
    b = tf.Variable(tf.zeros([n_neurons]), name=f'b_{name}')
    
    layer = tf.matmul(input, W) + b
    
    if activation == 'relu':
        layer = tf.nn.relu(layer)
        
    if name:
        # Add a name to the layer output tensor (useful for extraction)
        layer = tf.identity(layer, name=name) 
        
    return layer

def load_image (image_path):
    """Loads an image using GDAL."""
    log_message (f" [INFO] Loading image from path: {image_path}")
    dataset = gdal.Open(image_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise FileNotFoundError (f"Error loading image: {image_path}. Check the path.")
    return dataset

def convert_to_array (dataset):
    """Converts a GDAL dataset to a NumPy array."""
    log_message (" [INFO] Converting dataset to NumPy array")
    bands_data = [dataset.GetRasterBand (i + 1).ReadAsArray() for i in range(dataset.RasterCount)]
    stacked_data = np.stack (bands_data, axis=2)
    return stacked_data

def reshape_single_vector(data_classify):
    """Reshapes the input data into a single-pixel vector."""
    return data_classify.reshape([data_classify.shape[0] * data_classify.shape [1], data_classify.shape[2]])

def reproject_geometry (geom, src_crs, dst_crs):
    """Reprojects a geometry."""
    project = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True).transform
    return transform(project, geom)

def has_significant_intersection (geom, image_bounds, min_intersection_area=0.01):
    """Checks for a significant intersection."""
    geom_shape = shape (geom)
    image_shape = box (*image_bounds)
    intersection = geom_shape.intersection (image_shape)
    return intersection.area >= min_intersection_area

def clip_image_by_grid(geom, image, output, buffer_distance_meters=100, max_attempts=5, retry_delay=5):
    """Clips an image using a geometry (grid) with a buffer."""
    # (Full implementation of clip_image_by_grid as in A_3_1)
    attempt = 0
    while attempt < max_attempts:
        try:
            log_message(f" [INFO] Attempt {attempt+1}/{max_attempts} to clip image: {image}")
            with rasterio.open(image) as src:
                image_crs = src.crs
                geom_shape = shape(geom)
                geom_proj = reproject_geometry(geom_shape, 'EPSG:4326', image_crs)
                expanded_geom = geom_proj.buffer(buffer_distance_meters)
                expanded_geom_geojson = mapping(expanded_geom)

                if has_significant_intersection(expanded_geom_geojson, src.bounds):
                    out_image, out_transform = mask(src, [expanded_geom_geojson], crop=True, nodata=np.nan, filled=True)
                    out_meta = src.meta.copy()
                    out_meta.update({"driver": "GTiff", "height": out_image.shape[1], "width": out_image.shape[2],
                                     "transform": out_transform, "crs": src.crs})

                    with rasterio.open(output, 'w', **out_meta) as dest:
                        dest.write(out_image)
                    log_message(f" [INFO] Image clipped successfully: {output}")
                    return True 
                else:
                    log_message(f" [INFO] Insufficient overlap for clipping: {image}")
                    return False
        except Exception as e:
            log_message(f" [ERROR] Error during clipping: {str(e)}. Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
            attempt += 1
    log_message(f" [ERROR] Failed to clip image after {max_attempts} attempts: {image}")
    return False

def build_vrt (vrt_path, input_tif_list):
    """Builds a VRT from a list of TIFFs."""
    # (Full implementation of build_vrt as in A_3_1)
    if isinstance (input_tif_list, str):
        input_tif_list = input_tif_list.split()
    # (Missing-file check omitted)
    if os.path.exists(vrt_path):
        os.remove(vrt_path)
    
    gdal.BuildVRT (vrt_path, input_tif_list)

def translate_to_tiff (vrt_path, output_path):
    """Translates a VRT into an optimized TIFF."""
    # (Full implementation of translate_to_tiff as in A_3_1)
    if os.path.exists (output_path):
        os.remove (output_path)
        
    options = gdal.TranslateOptions (
        format="GTiff",
        creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "COPY_SRC_OVERVIEWS=YES", "BIGTIFF=YES"],
        noData=0
    )
    result = gdal.Translate (output_path, vrt_path, options=options)
    if result is None:
        raise RuntimeError (f"Failed to translate VRT to TIFF: {output_path}")

def generate_optimized_image (name_out_vrt, name_out_tif, files_tif_list, suffix=""):
    """Generates the final optimized TIFF from merging smaller TIFFs."""
    # (Full implementation of generate_optimized_image as in A_3_1)
    try:
        name_out_vrt_suffixed = name_out_vrt.replace(".tif", f"{suffix}.vrt") if suffix else name_out_vrt.replace(".tif", ".vrt")
        name_out_tif_suffixed = name_out_tif.replace(".tif", f"{suffix}.tif") if suffix else name_out_tif
        
        build_vrt (name_out_vrt_suffixed, files_tif_list)
        translate_to_tiff (name_out_vrt_suffixed, name_out_tif_suffixed)
        return True
    except Exception as e:
        log_message (f" [ERROR] Failed to generate optimized image. {e}")
        return False

def check_or_create_collection(collection, ee_project):
    """Checks for and creates a GEE collection if it does not exist."""
    check_command = f'earthengine --project {ee_project} asset info {collection}'
    status = os.system(check_command)
    if status != 0:
        create_command = f'earthengine --project {ee_project} create collection {collection}'
        os.system(create_command)

def clean_directories (directories_to_clean):
    """Cleans and recreates the given directories."""
    for directory in directories_to_clean:
        if os.path.exists (directory):
            shutil.rmtree (directory)
        os.makedirs (directory)

def remove_temporary_files(files_to_remove):
    """Removes temporary files."""
    for file in files_to_remove:
        if os.path.exists (file):
            try:
                os.remove (file)
            except Exception:
                pass


# EMBEDDING CORE FUNCTIONS


def create_embedding_model_graph (hyperparameters):
    """
    Builds the TensorFlow computational graph adapted for EMBEDDING extraction.
    Uses tf.variable_scope so the network is rebuilt with the
    same variable names as the checkpoint (saved via A_2_1/A_3_1).
    """
    
    graph = tf.Graph()
    with graph.as_default():
        # Define placeholders
        x_input = tf.placeholder(tf.float32, shape=[None, hyperparameters['NUM_INPUT']], name='x_input')
        y_input = tf.placeholder(tf.int64, shape=[None], name='y_input')
        
        # Normalize the data
        normalized = (x_input - hyperparameters['data_mean']) / hyperparameters['data_std']
        
        # Build the layers (USING SCOPE TO RESTORE THE CHECKPOINT NAMES)
        
        with tf.variable_scope('hidden1'):
            hidden1 = fully_connected_layer (normalized, n_neurons=hyperparameters['NUM_N_L1'], activation='relu', name='h1')
        with tf.variable_scope('hidden2'):
            hidden2 = fully_connected_layer (hidden1, n_neurons=hyperparameters['NUM_N_L2'], activation='relu', name='h2')
        with tf.variable_scope('hidden3'):
            hidden3 = fully_connected_layer (hidden2, n_neurons=hyperparameters['NUM_N_L3'], activation='relu', name='h3')
        with tf.variable_scope('hidden4'):
            hidden4 = fully_connected_layer (hidden3, n_neurons=hyperparameters['NUM_N_L4'], activation='relu', name='h4')
        
        # DEFAULT OUTPUT POINT (L5)
        with tf.variable_scope('hidden5'): 
            embedding_output = fully_connected_layer (hidden4, n_neurons=hyperparameters['NUM_N_L5'], activation='relu', name='embedding_output_L5')
        
        # The output tensor for the L5 extraction:
        outputs = embedding_output
        tf.identity (outputs, name='extracted_embedding') # Tensor L5: 'extracted_embedding:0'
        
        # The rest of the graph (logits) is kept to load the checkpoint
        with tf.variable_scope('logits'):
            logits = fully_connected_layer (embedding_output, n_neurons=hyperparameters['NUM_CLASSES'])
        
        # Optimizer, Loss and Saver (needed for the restore)
        cross_entropy = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=logits, labels=y_input))
        optimizer = tf.train.AdamOptimizer(hyperparameters['lr']).minimize(cross_entropy)
        
        saver = tf.train.Saver()
        return graph, {'x_input': x_input, 'y_input': y_input}, saver
        
def classify_for_embeddings (data_classify_vector, model_path, hyperparameters, selected_layer, block_size=40000000):
    """Extracts embeddings from the data in blocks, fetching the tensor of the chosen layer."""
    
    num_pixels = data_classify_vector.shape[0]
    num_blocks = (num_pixels + block_size - 1) // block_size
    output_blocks = []

    # Mapping from the chosen layer name to the tensor name in the graph
    tensor_map = {
        'h1': 'h1:0',
        'h2': 'h2:0',
        'h3': 'h3:0',
        'h4': 'h4:0',
        'h5': 'extracted_embedding:0' # L5
    }
    tensor_name = tensor_map.get(selected_layer, 'extracted_embedding:0') # Default to L5

    for i in range (num_blocks):
        start_idx = i * block_size
        end_idx = min((i + 1) * block_size, num_pixels)
        data_block = data_classify_vector[start_idx:end_idx]

        tf.compat.v1.reset_default_graph()
        graph, placeholders, saver = create_embedding_model_graph (hyperparameters)
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.50)
        
        with tf.Session (graph=graph, config=tf.ConfigProto(gpu_options=gpu_options)) as sess:
            saver.restore(sess, model_path)
            
            # Fetch the DYNAMIC embedding tensor
            output_block = sess.run(
                graph.get_tensor_by_name(tensor_name),
                feed_dict={placeholders['x_input']: data_block}
            )
            output_blocks.append (output_block)

    output_data_classify = np.concatenate (output_blocks, axis=0)
    return output_data_classify

def convert_to_raster_multiband (dataset_classify, embedding_data_scene_hwc, output_image_name):
    """Saves the multiband array (Embeddings) as GeoTIFF, normalized to uint8 (0-255)."""
    
    rows, cols, bands = embedding_data_scene_hwc.shape
    driver = gdal.GetDriverByName('GTiff')
    
    # Normalization from 0 to 255 (8-bit quantization)
    min_vals = np.min(embedding_data_scene_hwc, axis=(0, 1), keepdims=True)
    max_vals = np.max(embedding_data_scene_hwc, axis=(0, 1), keepdims=True)
    range_vals = max_vals - min_vals
    range_vals[range_vals == 0] = 1e-6 # Avoid division by zero
    
    normalized_data = 255 * (embedding_data_scene_hwc - min_vals) / range_vals
    embedding_data_scene_uint8 = normalized_data.astype ('uint8')
    
    # Transpose to the (C, H, W) layout GDAL expects
    embedding_data_chw = np.transpose (embedding_data_scene_uint8, (2, 0, 1))
    
    options = ['COMPRESS=DEFLATE', 'PREDICTOR=2', 'TILED=YES', 'BIGTIFF=YES']
    
    # Create the output dataset with the right number of bands (gdal.GDT_Byte for uint8)
    outDs = driver.Create (output_image_name, cols, rows, bands, gdal.GDT_Byte, options=options)
    
    # Write each embedding band
    for i in range (bands):
        outDs.GetRasterBand (i + 1).WriteArray (embedding_data_chw[i])
        
    outDs.SetGeoTransform (dataset_classify.GetGeoTransform())
    outDs.SetProjection (dataset_classify.GetProjection())
    outDs.FlushCache()
    outDs = None
    
    return True

def upload_embedding_to_gee (gcs_path, asset_id, satellite, region, year, version, ee_project):
    """Uploads multiband Embeddings to GEE."""
    
    timestamp_start = int(datetime(year, 1, 1).timestamp() * 1000)
    timestamp_end = int(datetime(year, 12, 31).timestamp() * 1000)
    creation_date = datetime.now().strftime('%Y-%m-%d')
    
    # Logic to check for and delete an existing asset (omitted for brevity)
    # ...
    
    # Perform the upload using Earth Engine CLI
    upload_command = (
        f'earthengine --project {ee_project} upload image --asset_id={asset_id} '
        f'--pyramiding_policy-mode '
        f'--property satellite={satellite} '
        f'--property region={region} '
        f'--property year={year} '
        f'--property version={version} '
        f'--property source=IPAM '
        f'--property type=annual_embedding ' # ASSET TYPE CHANGED
        f'--property time_start={timestamp_start} '
        f'--property time_end={timestamp_end} '
        f'--property create_date={creation_date} '
        f'{gcs_path}'
    )
    
    status = os.system(upload_command)
    return status == 0


# EMBEDDING PROCESSING WORKFLOWS

def process_single_image_embedding (dataset_classify, version, region, folder_temp, embedding_layer, country, bucket_name, fs):
    """Processes a single image, extracting the DNN embedding from layer 'embedding_layer'."""
    
    # We assume 'country', 'bucket_name', 'fs' are in the global scope or were passed in.
    
    # 1. Preparation: download the model (identical to A_3_1)
    gcs_model_file = f'gs://{bucket_name}/sudamerica/{country}/models_col1/col1_{country}_{version}_{region}_rnn_lstm_ckpt*'
    model_file_local_temp = f'{folder_temp}/col1_{country}_{version}_{region}_rnn_lstm_ckpt'
    
    try:
        subprocess.run(f'gsutil cp {gcs_model_file} {folder_temp}', shell=True, check=True)
        time.sleep(2)
        fs.invalidate_cache()
    except Exception:
        log_message(" [ERROR] Failed to download model from GCS.")
        return None
        
    # 2. Load hyperparameters (identical to A_3_1)
    json_path = f'{folder_temp}/col1_{country}_{version}_{region}_rnn_lstm_ckpt_hyperparameters.json'
    with open (json_path, 'r') as json_file:
        hyperparameters = json.load(json_file)
    
    # 3. Data conversion and vectorization (identical to A_3_1)
    data_classify = convert_to_array (dataset_classify)
    data_classify_vector = reshape_single_vector(data_classify)
    
    # 4. Run the extraction (CALLS THE MODIFIED FUNCTION)
    output_data_classified = classify_for_embeddings (
        data_classify_vector, 
        model_file_local_temp, 
        hyperparameters,
        embedding_layer # Passa a camada escolhida
    )
    
    # 5. Reshape back to a multiband image layout (H, W, C)
    H, W = data_classify.shape[:2]
    C = output_data_classified.shape[-1] 
    
    output_image_data_hwc = output_data_classified.reshape ([H, W, C])
    
    # 6. Return the HWC embedding array
    return output_image_data_hwc


def process_year_by_satellite_embedding (satellite_years, bucket_name, folder_mosaic, folder_temp, suffix,
                                         ee_project, country, version, region, simulate_test=False, embedding_layer='h5'):
    """
    Main workflow for embedding generation, adapted for a dynamic layer.
    (Based on A_4_1 and A_3_1)
    """

    # 1. Setup inicial
    # 'fs' (gcsfs.GCSFileSystem) is assumed to be defined globally in A_4_1
    fs = gcsfs.GCSFileSystem(project=bucket_name)

    # ee.FeatureCollection and ee.ImageCollection are assumed to be accessible (EE initialized)
    try:
        grid = ee.FeatureCollection(f'projects/mapbiomas-{country}/assets/FIRE/AUXILIARY_DATA/GRID_REGIONS/grid-{country}-{region}')
        grid_landsat = grid.getInfo()['features']
    except Exception as e:
        log_message(f"[ERROR] Falha ao carregar grid do GEE: {e}")
        return

    start_time = time.time()
    
    # Define the new GEE collection for embeddings
    collection_name = f'projects/{ee_project}/assets/FIRE/COLLECTION1/CLASSIFICATION_EMBEDDINGS/embedding_field_{country}_{version}'
    check_or_create_collection (collection_name, ee_project)
    
    for satellite_year in satellite_years:
        satellite = satellite_year['satellite']
        # Limit to 1 year when simulating
        years = satellite_year['years'][:1] if simulate_test else satellite_year['years']
        
        with tqdm (total=len(years), desc=f'Processing years for satellite {satellite.upper()}') as pbar_years:
            for year in years:
                test_tag = "_test" if simulate_test else ""
                
                # New TIFF file name for Embeddings
                image_name = f"embedding_{country}_{satellite}_{version}_region{region[1:]}_{year}{suffix}{test_tag}"
                gcs_filename = f'gs://{bucket_name}/sudamerica/{country}/result_embeddings/{image_name}.tif'
                # Path to the COG mosaic (using the iXX_country_rY_year_cog.tif format)
                local_cog_path = f'{folder_mosaic}/{satellite}_{country}_{region}_{year}_cog.tif'
                # Corrected path to models_col1_cog (assuming this is the convention)
                gcs_cog_path = f'gs://{bucket_name}/sudamerica/{country}/mosaics_col1_cog/{satellite}_{country}_{region}_{year}_cog.tif' 

                # 2. Download the COG
                if not os.path.exists (local_cog_path):
                    try:
                        os.system(f'gsutil cp {gcs_cog_path} {local_cog_path}')
                        time.sleep(2)
                        fs.invalidate_cache()
                    except Exception as e:
                        log_message(f"[ERROR] Falha ao baixar COG: {gcs_cog_path}. {e}")
                        continue
                
                input_scenes = []
                # Limit to 1 scene when simulating
                grids_to_process = [grid_landsat[0]] if simulate_test else grid_landsat
                
                with tqdm (total=len(grids_to_process), desc=f'Processing scenes for year {year}') as pbar_scenes:
                    for grid_feature in grids_to_process:
                        orbit = grid_feature['properties']['ORBITA']
                        point = grid_feature['properties']['PONTO']
                        # Different TEMP name to avoid clashing with the classification
                        output_image_name = f'{folder_temp}/image_emb_{country}_{region}_{version}_{orbit}_{point}_{year}.tif'
                        geometry_scene = grid_feature['geometry']
                        # Different TEMP name for the clip
                        NBR_clipped = f'{folder_temp}/image_mosaic_emb_clipped_{orbit}_{point}_{year}.tif' 

                        if os.path.isfile (output_image_name):
                            pbar_scenes.update(1)
                            continue
                        
                        # 3. Image clipping
                        clipping_success = clip_image_by_grid(geometry_scene, local_cog_path, NBR_clipped)
                        
                        if clipping_success:
                            dataset_classify = load_image (NBR_clipped)
                            
                            # 4. Embedding extraction (CALLS THE FUNCTION WITH THE NEW PARAMETER)
                            image_data_hwc = process_single_image_embedding (
                                dataset_classify, 
                                version, 
                                region, 
                                folder_temp, 
                                embedding_layer, # Embedding layer
                                country, 
                                bucket_name, 
                                fs
                            )
                            
                            # --- FLOW FIX: CHECK WHETHER THE EMBEDDING WAS GENERATED (DID NOT RETURN None) ---
                            if image_data_hwc is None:
                                log_message(f"[ERROR] Extração de embedding falhou (provavelmente download do modelo) para cena {orbit}/{point}. Pulando.")
                                pbar_scenes.update(1)
                                remove_temporary_files([NBR_clipped]) # Limpa o clip
                                continue # Skip to the next scene
                            # --- END OF FLOW FIX ---

                            # 5. Conversion to a multiband raster
                            convert_to_raster_multiband (dataset_classify, image_data_hwc, output_image_name)
                            input_scenes.append(output_image_name)
                            remove_temporary_files([NBR_clipped])
                        
                        pbar_scenes.update(1)

                # 6. Generation of the optimized multiband TIFF (VRT merge)
                if input_scenes:
                    input_scenes_str = " ".join(input_scenes)
                    merge_output_temp = f"{folder_temp}/merged_emb_temp_{year}.tif"
                    output_image = f"{folder_temp}/{image_name}.tif"
                    generate_optimized_image (merge_output_temp, output_image, input_scenes_str)
                    
                    # 7. Upload to GCS and GEE
                    status_upload = os.system(f'gsutil cp {output_image} {gcs_filename}')
                    time.sleep(2)
                    fs.invalidate_cache()
                    
                    if status_upload == 0 and os.system(f'gsutil ls {gcs_filename}') == 0:
                        upload_embedding_to_gee (
                            gcs_filename,
                            f'{collection_name}/{image_name}',
                            satellite, region, year, version, ee_project
                        )
                        
                    clean_directories ([folder_temp])
                
                elapsed = time.time() - start_time
                log_message(f"[INFO] Year {year} embedding generation completed. Time: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
                pbar_years.update(1)


# MAIN EXECUTION LOGIC (render_embedding_models)
def render_embedding_models(models_to_process, simulate_test=False):
    """Processes a list of models and mosaics to extract Embeddings."""
    
    bucket_name_global = 'mapbiomas-fire'
    # Mock of fs and ee_project (they must be defined in your notebook scope)
    ee_project_global = 'mapbiomas-colombia'
    
    for model_info in models_to_process:
        
        model_name = model_info["model"]
        mosaics = model_info ["mosaics"]
        simulation = model_info["simulation"]
        embedding_layer = model_info["embedding_layer"] # Extrai a camada
        
        try:
            parts = model_name.split('_')
            country = parts[1]
            version = parts[2]
            region = parts[3].split('.')[0]
        except Exception:
            log_message(f"[ERROR] Não foi possível extrair info do modelo: {model_name}")
            continue

        folder = f'/content/mapbiomas-fire/sudamerica/{country}'
        folder_temp = f'{folder}/tmp_emb' 
        folder_mosaic = f'{folder}/mosaics_cog'
        
        for directory in [folder_temp, folder_mosaic]:
            if not os.path.exists (directory):
                os.makedirs (directory)
        
        clean_directories ([folder_temp, folder_mosaic])

        satellite_years = []
        for mosaic in mosaics:
            mosaic_parts = mosaic.split('_')
            
            # --- YEAR PARSING FIX ---
            try:
                # The mosaic format is I89_guyana_r5_2022_cog.tif
                satellite = mosaic_parts[0]
                
                # The year is the fourth part (index 3)
                year_str = mosaic_parts[3]
                
                # Make sure only the 4 digits are taken and converted to int
                import re
                year_match = re.search(r'\d{4}', year_str)

                if year_match:
                    year = int(year_match.group(0))
                else:
                    log_message(f"[ERROR A_4_1] Não foi possível extrair o ano (4 dígitos) do mosaico: {mosaic}")
                    continue
                
                satellite_years.append({"satellite": satellite, "years": [year]})
                
            except Exception as e:
                log_message(f"[ERROR A_4_1] Falha ao processar o nome do mosaico {mosaic}: {e}")
                continue
        
        if not simulation:            process_year_by_satellite_embedding (
                satellite_years=satellite_years,
                bucket_name=bucket_name_global,
                folder_mosaic=folder_mosaic,
                folder_temp=folder_temp,
                suffix='',
                ee_project=ee_project_global, # Use your real global variable
                country=country,
                version=version,
                region=region,
                simulate_test=simulate_test,
                embedding_layer=embedding_layer # Passa a camada
            )
