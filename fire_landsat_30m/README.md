# 🔥 MapBiomas Fire Landsat 30m

This module contains the workflows, scripts, and methodological configurations used for burned-area classification with **30-meter Landsat imagery** as part of the **MapBiomas Fire** initiative.

The workflow supports the processing and classification of satellite imagery across different ecoregions, with parameters and configurations adapted to each region.

## 🛰️ Data and Processing

The classification workflow is based on Landsat imagery and is implemented primarily in **Google Earth Engine (GEE)** and **Google Colab**.

## 🔁 Workflow

1. **Sample collection** — collect and review training samples in the GEE toolkit (`1-Toolkit_Collection1`).
2. **Classification** — train the TensorFlow models per region and classify the burned areas in Google Colab (`2-classification_algorithms`).
3. **Final products** — post-process the classifications (LULC masks) and generate the annual collection and its subproducts (`3-Collection_anual_final_products`).

## 📂 Repository Structure

```
collection_01/
├── 00_Tools/
│   ├── Legends.js                                    # Legend dictionaries (fire regions, months, Colombia LULC levels)
│   └── Palettes.js                                   # Color palettes for the fire products and LULC
├── 1-Toolkit_Collection1/
│   ├── Toolkit_samples_collection.js                 # GEE app to collect and evaluate training samples and export mosaics
│   └── Visualize-Collections-Fire.js                 # GEE app to visualize the final fire products
├── 2-classification_algorithms/
│   ├── A_0_0_oficial_gitclone.py                     # Clone this repository into Colab
│   ├── A_0_1_basic_authentication_and_parameterization.py  # Authentication and country parameters
│   ├── A_0_2_log_algorithm_monitor.py                # Execution log and monitoring
│   ├── A_0_3_simple_gui_to_gcs_explorer_optional.py  # GUI to browse Cloud Storage (optional)
│   ├── A_1_0_gee_gui_collect_samples_burned_area_classification.py  # Link to the sample collection toolkit
│   ├── A_2_0_simple_gui_train_tensorflow_models.py   # GUI for model training
│   ├── A_2_1_training_tensorflow_model_per_region.py # Model training by region
│   ├── A_3_0_simple_gui_train_tensorflow_classification.py  # GUI for classification
│   ├── A_3_1_tensorflow_classification_burned_area.py       # Burned-area classification
│   ├── A_4_0_simple_gui_feature_maps_of_classification.py   # GUI for feature maps
│   └── A_4_1_tensorflow_feature_maps_extraction.py          # Feature map extraction
└── 3-Collection_anual_final_products/
    ├── 1.1-Col1-Post_classifications/
    │   ├── 1-final_classifications_col1_no_masks.js                       # Copy the final classifications into the no-mask collection
    │   └── 2-export_col1_masks_lulc_and_pixel_date_colombia_regiones.js   # Apply LULC masks and export the masked collection
    └── 1.2-Collection1_Fire_Subproducts/
        ├── 1_burned_area_products_monthly_annual_coverage.js   # Monthly/annual burned area and burned coverage
        ├── 2_burned_area_frequency_accumulated_coverage.js     # Fire frequency and accumulated burned area
        ├── 3_year_last_fire.js                                  # Year of the last fire
        ├── 4-export_vectorization_annual_burned.js              # Annual binary rasters for vectorization
        ├── 5-export_annual_burned_id_and_size_by_year.js        # Scar ID and scar area (ha) by year
        └── 6-export_scar_size_range_by_year.js                  # Scar size range classes by year
```

> The exact structure and parameters may vary by collection and ecoregion.

## 🚀 Prerequisites

- A **Google Earth Engine** account ([sign up here](https://earthengine.google.com/)).
- Basic familiarity with the Google Earth Engine Code Editor.
- A **Google Colab** environment and access to the `mapbiomas-fire` Cloud Storage bucket (for `2-classification_algorithms`).
- Access to the required MapBiomas Fire input assets and datasets.
