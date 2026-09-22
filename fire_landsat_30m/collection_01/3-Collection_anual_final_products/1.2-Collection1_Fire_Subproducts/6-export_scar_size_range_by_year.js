/***************************************************************
 MAPBIOMAS FIRE - SUBPRODUCT: SCAR SIZE CLASSIFICATION (ANNUAL BURNED AREA)

 📅 DATE: April 2025

 TEAM:
 Fire scar mapping network - MapBiomas Fire
 - Instituto de Pesquisa Ambiental da Amazonia (IPAM)
 - Wallace Silva and Vera Arruda; wallace.silva@ipam.org.br, vera.arruda@ipam.org.br
 -------------------------------------------------------------
 📌 WHAT DOES THIS SCRIPT DO?
 From the annual burned area product (ha), it classifies the pixels
 into scar size ranges and exports the result as a categorical raster
 to the Assets. It also displays a visualization and a legend.

 🔴 Products generated:
 - `scar_size_ranges` (8 classes): annual scar size ranges (in hectares).

 🧠 Logic (summary):
 1) Load the multiband image of annual burned area (ha).
 2) Assign classes (1 to 8) according to scar size thresholds (ha).
 3) Define the color palette for the ranges.
 4) Visualize the last band and the range classification.
 5) Export the classified image to Assets.
 6) Build and add a legend panel (UI) with one label per class.

 -------------------------------------------------------------
 🔧 WHAT SHOULD I MODIFY TO USE THIS SCRIPT?
 ✅ Check the input/output paths (input asset and output `assetId`).
 ✅ Adjust `region` (geometry), `scale` and `maxPixels` for the country/study area.
 ✅ Update `description` if a different naming convention is required.

 🗒️ Notes:
 - The size classes are defined in hectares (ha).
 - The "slice(-1)" visualization shows the last available band.
 - The UI legend reflects the defined ranges (1 to 8).

***************************************************************/

// Header 
print('Burned Area Analysis by Size Range');

var country = 'colombia'; // 'bolivia','chile','colombia','ecuador','guyana','paraguay','peru','venezuela'
var coll_n = '1';

// 📥 input asset
var scar_size = ee.Image('projects/mapbiomas-' + country +'/assets/FIRE/COLLECTION' + coll_n +
  '/FINAL_PRODUCTS/mapbiomas_' + country +'_fire_collection' + coll_n + '_annual_burned_area_ha_v2');

// 📍 region
var regions = ee.FeatureCollection('projects/mapbiomas-' + country +
  '/assets/FIRE/AUXILIARY_DATA/regiones_fuego_' + country + '_v1');
var geometry = regions.union().geometry().bounds();

// 📤 output
var description = 'mapbiomas_' + country + '_fire_collection' + coll_n + '_annual_burned_scar_size_range_v2';
var assetId = 'projects/mapbiomas-' + country +
  '/assets/FIRE/COLLECTION' + coll_n + '/FINAL_PRODUCTS/' + description;

//📊 PROCESSING (UNCHANGED)

// header
print('Burned Area Analysis by Size Range');

// print image
print('Burned area size:', scar_size);

// visualize last band
Map.addLayer(scar_size.slice(-1), { min: 0, max: 1000 }, 'Burned Area Size (km²)', false);

// classification
// var scar_size_ranges = scar_size
//   .multiply(0)
//   .where(scar_size.lt(5), 1)
//   .where(scar_size.gte(5), 2)
//   .where(scar_size.gte(25), 3)
//   .where(scar_size.gte(50), 4)
//   .where(scar_size.gte(250), 5)
//   .where(scar_size.gte(500), 6)
//   .where(scar_size.gte(1000), 7)
//   .where(scar_size.gte(5000), 8);
var scar_size_ranges = scar_size
  .multiply(0)
  .where(scar_size.lt(0.5), 1)
  .where(scar_size.gte(0.5), 2)
  .where(scar_size.gte(1), 3)
  .where(scar_size.gte(2), 4)
  .where(scar_size.gte(5), 5)
  .where(scar_size.gte(20), 6)
  .where(scar_size.gte(200), 7)
  .where(scar_size.gte(2000), 8);
// palette
var palette = ["#d0ae35", "#c68e2f", "#b96d2b", "#ac4b28", "#933328", "#72251d", "#4e180f", "#240d00"];

// visualize classification
Map.addLayer(scar_size_ranges.slice(-1), {
  min: 1, max: 8, palette: ['#d0ae35','#c68e2f','#b96d2b',
                              '#ac4b28','#933328','#72251d',
                              '#4e180f','#240d00',] },
                              'Burned Area Size Ranges');

// export
Export.image.toAsset({
  image: scar_size_ranges,
  description: description,
  assetId: assetId,
  pyramidingPolicy: 'mode',
  region: geometry,
  scale: 30,
  maxPixels: 1e13
});

//🧾 LEGEND (UNCHANGED)

var labels_ha = [
  [1, '< 5 ha'],
  [2, '5 - 25 ha'],
  [3, '25 - 50 ha'],
  [4, '50 - 250 ha'],
  [5, '250 - 500 ha'],
  [6, '500 - 1,000 ha'],
  [7, '1,000 - 5,000 ha'],
  [8, '>= 5,000 ha']
];

var legendPanel = labels_ha.map(function(list, i) {
  return ui.Panel([
    ui.Label('O', { backgroundColor: palette[i], margin: '1px' }),
    ui.Label(list[0] + ': ' + list[1], { fontSize: 10, margin: '1px' })
  ], ui.Panel.Layout.Flow('horizontal'), { margin: '1px' });
});

var subtitle = ui.Panel([
  ui.Panel(legendPanel, ui.Panel.Layout.Flow('vertical'), { margin: '1px', stretch: 'both', border: '0.5px solid red' })
    .insert(0, ui.Label('LEGEND')),
], ui.Panel.Layout.Flow('vertical'), { margin: '1px', position: 'bottom-left' });

Map.add(subtitle);
