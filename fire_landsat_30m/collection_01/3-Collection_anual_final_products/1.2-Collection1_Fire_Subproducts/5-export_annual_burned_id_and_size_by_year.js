/***************************************************************  
 MAPBIOMAS FIRE - FREQUENCY AND ACCUMULATED BURNED AREAS

 📅 DATE: April 2026

 TEAM:
 Fire scar mapping network - MapBiomas Fire
 - Instituto de Pesquisa Ambiental da Amazonia (IPAM)
 - Wallace Silva and Vera Arruda; wallace.silva@ipam.org.br, vera.arruda@ipam.org.br

 -------------------------------------------------------------
 📌 WHAT DOES THIS SCRIPT DO?
 Generates two annual multiband images from the vector collections
 of scars accumulated by year (one FeatureCollection per year):

 1) `scar_id_YYYY`     : band with the ID of each scar burned in the year.
 2) `scar_area_ha_YYYY`: band with the area (ha) of each scar in the year.

 Each final image contains one band per year and is exported to the Asset.

 -------------------------------------------------------------
 🔧 WHAT SHOULD I MODIFY TO USE THIS SCRIPT?
 ✅ `assetOutput` (output folder).
 ✅ `address_to_replace_template`: per-year asset template with the 'YEAR' token.
 ✅ Year range in `ee.List.sequence(...)` if you want a different period.
 ✅ `region` if you want to clip or change the export AOI.
***************************************************************/

/* ===========================
 🔧 CONFIGURATION BLOCK
 =========================== */

var country = 'colombia'; // 'bolivia','chile','colombia','ecuador','guyana','paraguay','peru','venezuela'
var coll_n = '1';

// 🧾 Output names
var outFileName_id       = 'mapbiomas_' + country + '_fire_collection' + coll_n + '_annual_burned_id_v1';
var outFileName_area_ha  = 'mapbiomas_' + country + '_fire_collection' + coll_n + '_annual_burned_area_ha_v1';

// 🗂️ Output folder
var assetOutput = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION' + coll_n + '/FINAL_PRODUCTS/';

// ⚙️ Export parameters
var scale = 30;

// 📍 Export region 
var regions = ee.FeatureCollection('projects/mapbiomas-' + country + '/assets/FIRE/AUXILIARY_DATA/regiones_fuego_' + country + '_v1');
var geometry = regions.union().geometry().bounds();

// 🔗 Per-year asset template (IMPORTANT: it must contain the 'YEAR' token)
var address_to_replace_template = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION' + coll_n + '/FINAL_PRODUCTS/annual-burned-vectors/mbfogo-col' + coll_n + '-YEAR-v1';

// 🗓️ Year range to process
ee.List.sequence(2000, 2025, 1).evaluate(function(years) {

  // 🧱 Initialize empty images (no bands)
  var scar_id   = ee.Image().select();
  var scar_area = ee.Image().select();

  years.forEach(function(year) {

    // 📥 Load the year's FeatureCollection (replacing YEAR in the template)
    var address_for_year = address_to_replace_template.replace('YEAR', '' + year);

    var scar_vector_year = ee.FeatureCollection(address_for_year)
      .map(function(feat){
        return feat.set({
          area_ha: feat.geometry().area().int().divide(10000)
        });
      });

    // 🖌️ Paint the scar ID as band 'scar_id_YYYY'
    scar_id = scar_id.addBands(
      ee.Image().paint(scar_vector_year, 'id')
        .rename('scar_id_' + year)
        .int()
    );

    // 🖌️ Paint the scar area (ha) as band 'scar_area_ha_YYYY'
    scar_area = scar_area.addBands(
      ee.Image().paint(scar_vector_year, 'area_ha')
        .rename('scar_area_ha_' + year)
        .float()
    );

  });

  // 👀 Visualization: all bands
  Map.addLayer(scar_id.aside(print, 'scar_id (todas bandas)'), {}, 'All Scar ID', false);
  Map.addLayer(scar_area.aside(print, 'scar_area_ha (todas bandas)'), {}, 'All Scar Area', false);

  // 👀 Visualization: last band
  var lastIdBandName   = ee.String(scar_id.bandNames().get(ee.Number(scar_id.bandNames().length()).subtract(1)));
  var lastAreaBandName = ee.String(scar_area.bandNames().get(ee.Number(scar_area.bandNames().length()).subtract(1)));

  Map.addLayer(scar_id.select([lastIdBandName]).randomVisualizer().aside(print, 'scar_id (última banda)'), {}, 'Scar ID (última)');
  Map.addLayer(scar_area.select([lastAreaBandName]).aside(print, 'scar_area_ha (última banda)'), { min: 100, max: 100000 }, 'Scar Area (última)');
  
  // 🚀 Exports 
  [
    [outFileName_id,      scar_id,   'mode'],
    [outFileName_area_ha, scar_area, 'median'],
  ].forEach(function(spec) {

    Export.image.toAsset({
      image: spec[1],
      description: 'GT_Fogo-' + spec[0],
      assetId: assetOutput + spec[0],
      pyramidingPolicy: spec[2],
      region: geometry,
      scale: scale,
      maxPixels: 1e11
    });
  });
});