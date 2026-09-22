/***************************************************************
 MAPBIOMAS FIRE - SUBPRODUCT: ANNUAL BURNED AREA (BINARY)

 📅 DATE: April 21, 2026

 TEAM:
 Fire scar mapping network - MapBiomas Fire
 - Instituto de Pesquisa Ambiental da Amazonia (IPAM)

 -------------------------------------------------------------
 📌 WHAT DOES THIS SCRIPT DO?
 Generates annual binary images (1 = fire, 0 = no fire)
 from the annual burned area collection.

 -------------------------------------------------------------
 🔧 REQUIRED SETUP:
 ✅ Define `country`
 ✅ Check the automatic paths (assetInput / regions)
 ✅ Adjust the export folder (Drive)

***************************************************************/

print(
  ui.Label('Después de exportar los rásters anuales binarios, utilice el notebook de Colab para vectorizar.'),
  ui.Label('Google Colab', {}, 'https://colab.research.google.com/drive/1JVQMcTVbj9hRA8iIFMteTl86e4FmX_4E?usp=sharing')
);


// 🔧 CONFIGURATION BLOCK

var country = 'colombia'; // 'bolivia', 'chile', 'colombia', 'ecuador', 'guyana', 'paraguay', 'peru', 'venezuela'
var coll_n = '1';

// 🔥 Annual burned area asset
var assetInput = 'projects/mapbiomas-' + country +  '/assets/FIRE/COLLECTION' + coll_n +
  '/FINAL_PRODUCTS/mapbiomas_' + country +  '_fire_collection' + coll_n + '_annual_burned_v1';

// 📍 Auxiliary regions (for clipping)
var regions = ee.FeatureCollection('projects/mapbiomas-' + country +  '/assets/FIRE/AUXILIARY_DATA/regiones_fuego_' + country + '_v1');

// 📤 Export settings
var folder = 'mapbiomas_fire_collection' + coll_n + '_' + country;
var scale = 30;

//📥 INPUT LOADING AND PREPARATION
var fire = ee.Image(assetInput);
var geometry = regions.union().geometry().bounds();
print('🔥 Colección anual:', fire);
Map.centerObject(geometry, 5);

//🔁 AUTOMATIC EXPORT BY YEAR

fire.bandNames().evaluate(function(bandNames){
  bandNames.forEach(function(bandName){
    // Convert to binary (>=1 → 1)
    var fire_year = fire.select(bandName).gte(1).rename(bandName).uint8();
    var description = 'mapbiomas_' + country + '_fire_collection' + coll_n + '_' + bandName;

    Export.image.toDrive({
      image: fire_year,
      description: 'GT_Fuego-' + description,
      folder: folder,
      fileNamePrefix: description,
      region: geometry,
      scale: scale,
      maxPixels: 1e13
    });

  });

});

// VISUALIZATION (OPTIONAL)

// Visualize a specific year
var year_vis = 'burned_area_2025';

Map.addLayer(
  fire.select(year_vis),
  {min: 0, max: 1, palette: ['000000', 'FF0000']},
  '🔥 Fuego ' + year_vis,
  true
);
