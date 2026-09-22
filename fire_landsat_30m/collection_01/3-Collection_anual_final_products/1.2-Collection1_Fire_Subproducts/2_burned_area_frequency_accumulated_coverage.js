/***************************************************************
 MAPBIOMAS FIRE - FREQUENCY AND ACCUMULATED BURNED AREAS

 📅 DATE: May 6, 2025

 TEAM:
 Fire scar mapping network - MapBiomas Fire
 - Instituto de Pesquisa Ambiental da Amazonia (IPAM)
 - Wallace Silva and Vera Arruda; wallace.silva@ipam.org.br, vera.arruda@ipam.org.br

 -------------------------------------------------------------
 📌 WHAT DOES THIS SCRIPT DO?
 Generates and exports two accumulated fire products:

 🔴 `fireFrequency`: burn frequency (0-N), without LULC.
 🔵 `fireFrequencyCoverage`: frequency with LULC encoding.

 It also generates:
 🟡 `fireAccumulated`: binary 0/1 indicating accumulated burns.
 🟢 `fireAccumulatedCoverage`: LULC classes of the accumulated burned areas.

 -------------------------------------------------------------
 🔧 WHAT SHOULD I MODIFY TO USE THIS SCRIPT?
 ✅ Change the `country` variable if you want another country.
 ✅ Check the output paths and names.

***************************************************************/

// 🌍 Country under analysis
var country = 'colombia'; // 'bolivia', 'chile', 'colombia', 'paraguay', 'peru'
var coll_n = '1'; // '1', '2', '3'
// 🧾 Output file names
var outFileNameFrequency = 'mapbiomas_'+ country +'_fire_collection' + coll_n + '_frequency_burned_v1'; 
var outFileNameFrequencyCoverage = 'mapbiomas_'+ country +'_fire_collection' + coll_n + '_frequency_burned_coverage_v1'; 
var outFileNameAccumulated = 'mapbiomas_'+ country +'_fire_collection' + coll_n + '_accumulate' + coll_n + '_burned_v1'; 
var outFileNameAccumulatedCoverage = 'mapbiomas_'+ country +'_fire_collection' + coll_n + '_accumulated_burned_coverage_v1'; 

var assetOutput = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION1/FINAL_PRODUCTS/';

// 🗂️ Annual burned image
var assetFire = assetOutput + 'mapbiomas_'+ country +'_fire_collection'+coll_n+'_annual_burned_v1';
var annualBurned = ee.Image(assetFire);
print("annualBurned",annualBurned)
// 🌱 LULC collections by country
var landcover = {
  bolivia: ee.Image('projects/mapbiomas-public/assets/bolivia/lulc/collection3/mapbiomas_bolivia_collection3_integration_v1')
            .addBands(ee.Image('projects/mapbiomas-public/assets/bolivia/lulc/collection3/mapbiomas_bolivia_collection3_integration_v1').slice(-1).rename(['classification_2025'])),
  chile: ee.Image('projects/mapbiomas-public/assets/chile/collection1/mapbiomas_chile_collection1_integration_v1')
            .addBands(ee.Image('projects/mapbiomas-public/assets/chile/collection1/mapbiomas_chile_collection1_integration_v1').slice(-1).rename(['classification_2023']))
            .addBands(ee.Image('projects/mapbiomas-public/assets/chile/collection1/mapbiomas_chile_collection1_integration_v1').slice(-1).rename(['classification_2024'])),
  colombia: ee.Image('projects/mapbiomas-colombia/assets/LULC/COLECCION3/INTEGRACION/COLOMBIA-1')
            .addBands(ee.Image('projects/mapbiomas-colombia/assets/LULC/COLECCION3/INTEGRACION/COLOMBIA-1').slice(-1).rename(['classification_2025'])),
  paraguay: ee.Image('projects/mapbiomas-public/assets/paraguay/collection2/mapbiomas_paraguay_collection2_integration_v1')
            .addBands(ee.Image('projects/mapbiomas-public/assets/paraguay/collection2/mapbiomas_paraguay_collection2_integration_v1').slice(-1).rename(['classification_2024'])),
  peru: ee.Image('projects/mapbiomas-public/assets/peru/collection3/mapbiomas_peru_collection3_integration_v1')
};

var lulc = landcover[country];
print('lulc',lulc);
var geometry = lulc.geometry();

// 🧮 Generation of accumulated products 
annualBurned.bandNames().evaluate(function (bandnames) {
  
  var year_initial_band = bandnames[0].slice(-4);
  var year_final_band   = bandnames.slice(-1)[0].slice(-4);

  // -------- FORWARD: from year_initial_band → year_final_band --------
  var hitFirst  = annualBurned.select('burned_area_' + year_initial_band).gt(0);
  var countPrev = hitFirst.unmask(0); 
  var freqPrev  = countPrev 
    .rename('fire_frequency_' + year_initial_band + '_' + year_initial_band)
    .selfMask();

  var freqCovPrev = freqPrev.multiply(100)
    .add(lulc.select('classification_' + year_initial_band))
    .int16()
    .rename('fire_frequency_' + year_initial_band + '_' + year_initial_band);

  bandnames.slice(1).forEach(function (bandname) {
    var year = bandname.slice(-4);
    var hit  = annualBurned.select(bandname).gt(0);

    countPrev = countPrev.add(hit.unmask(0));

    var freq = countPrev 
      .rename('fire_frequency_' + year_initial_band + '_' + year).selfMask();

    var freqCoverage = freq.multiply(100)
      .add(lulc.select('classification_' + year))
      .int16();

    freqPrev    = freqPrev.addBands(freq);
    freqCovPrev = freqCovPrev.addBands(freqCoverage);
  });

  // -------- BACKWARD: from year_final_band ← year_initial_band --------
  var hitLast  = annualBurned.select('burned_area_' + year_final_band).gt(0);
  var countPost = hitLast.unmask(0);
  var freqPost  = countPost 
    .rename('fire_frequency_' + year_final_band + '_' + year_final_band)
    .selfMask();

  var freqCovPost = freqPost.multiply(100)
    .add(lulc.select('classification_' + year_final_band))
    .int16()
    .rename('fire_frequency_' + year_final_band + '_' + year_final_band);

  bandnames.slice(0, -1).reverse().forEach(function (bandname) {
    var year = bandname.slice(-4);
    var hit  = annualBurned.select(bandname).gt(0);

    countPost = countPost.add(hit.unmask(0));

    var freq = countPost 
      .rename('fire_frequency_' + year + '_' + year_final_band)
      .selfMask();

    var freqCoverage = freq.multiply(100)
      .add(lulc.select('classification_' + year))
      .int16();

    freqPost    = freqPost.addBands(freq);
    freqCovPost = freqCovPost.addBands(freqCoverage);
  });

  // 🔴 Frequency without and with land use
  var fireFrequency = freqPrev.addBands(freqPost.slice(0,-1)).select('fire_fre.*');
  fireFrequency = fireFrequency.select(fireFrequency.bandNames().sort());

  var fireFrequencyCoverage = freqCovPrev.addBands(freqCovPost.slice(0,-1)).select('fire_fre.*');
  fireFrequencyCoverage = fireFrequencyCoverage.select(fireFrequencyCoverage.bandNames().sort());

  // 🟡 Binary accumulated (0/1 indicating fire presence)
  var fireAccumulated = fireFrequency.gte(1).rename(
    fireFrequency.bandNames().map(function (bn) {
      return ee.String(bn).replace('fire_frequency_', 'fire_accumulated_');
    })
  );

  // 🟢 Accumulated with land use
  var fireAccumulatedCoverage = fireFrequencyCoverage
    .mod(100)
    .int()
    .rename(fireAccumulated.bandNames());

  // ✅ FINAL MASK: masks only the pixels that never burned over the whole period
  var everBurned = annualBurned.unmask(0).gt(0).reduce(ee.Reducer.sum()).gt(0);

  fireFrequency           = fireFrequency.selfMask();
  fireFrequencyCoverage   = fireFrequencyCoverage.selfMask();
  fireAccumulated         = fireAccumulated.selfMask();
  fireAccumulatedCoverage = fireAccumulatedCoverage.selfMask();

  // 📤 Verification print
  print('🔴 fireFrequency', fireFrequency);
  print('🔵 fireFrequencyCoverage', fireFrequencyCoverage);
  print('🟡 fireAccumulated', fireAccumulated);
  print('🟢 fireAccumulatedCoverage', fireAccumulatedCoverage);
  
  // 📤 Export of results
  Export.image.toAsset({
    image: fireFrequency,
    description: outFileNameFrequency,
    assetId: assetOutput + outFileNameFrequency,
    pyramidingPolicy: { '.default': 'mode' },
    region: geometry,
    scale: 30,
    maxPixels: 1e13
  });

  Export.image.toAsset({
    image: fireFrequencyCoverage,
    description: outFileNameFrequencyCoverage,
    assetId: assetOutput + outFileNameFrequencyCoverage,
    pyramidingPolicy: { '.default': 'mode' },
    region: geometry,
    scale: 30,
    maxPixels: 1e13
  });

  Export.image.toAsset({
    image: fireAccumulated,
    description: outFileNameAccumulated,
    assetId: assetOutput + outFileNameAccumulated,
    pyramidingPolicy: { '.default': 'mode' },
    region: geometry,
    scale: 30,
    maxPixels: 1e13
  });

  Export.image.toAsset({
    image: fireAccumulatedCoverage,
    description: outFileNameAccumulatedCoverage,
    assetId: assetOutput + outFileNameAccumulatedCoverage,
    pyramidingPolicy: { '.default': 'mode' },
    region: geometry,
    scale: 30,
    maxPixels: 1e13
  });

  // 🧪 Visualization (optional)
  var year = 2024;
  var fire_palettes = require('users/mapbiomasworkspace1/mapbiomas-fire:00_Tools/Palettes.js');
  var lulc_palettes = require('users/mapbiomas/modules:Palettes.js');

  Map.addLayer(
    annualBurned,
    {
      bands: 'burned_area_' + year,
      palette: ["800000"]
    },
    'Área quemada anual - ' + year
  );

  // 🔴 Frequency without LULC
  Map.addLayer(
    fireFrequency,
    {
      bands: 'fire_frequency_' + year_initial_band + '_' + year,
      min: 0,
      max: 25,
      palette: fire_palettes.get('frecuencia25')
    },
    '🔴 Frecuencia de fuego (sin LULC) - ' + year
  );
  
  // 🔵 LULC extracted from frequency + LULC
  Map.addLayer(
    fireFrequencyCoverage.mod(100).byte(),
    {
      bands: 'fire_frequency_' + year_initial_band + '_' + year,
      min: 0,
      max: 69,
      palette: lulc_palettes.get('classification9')
    },
    '🔵 Frecuencia de fuego - clase LULC ' + year
  );
  
  // 🟢 Accumulated + LULC combined
  Map.addLayer(
    fireAccumulatedCoverage,
    {
      bands: 'fire_accumulated_' + year_initial_band + '_' + year,
      min: 0,
      max: 69,
      palette: lulc_palettes.get('classification9')
    },
    '🟢 Fuego acumulado + clase LULC - valor combinado ' + year
  );
  
  // 🟡 Binary only of the accumulated (fire presence)
  Map.addLayer(
    fireAccumulated,
    {
      bands: 'fire_accumulated_' + year_initial_band + '_' + year,
      palette: ['ffffff', 'ff0000']
    },
    '🟡 Fuego acumulado - presencia binaria ' + year
  );

  
});