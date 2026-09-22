
/***************************************************************
 MAPBIOMAS FIRE - SUBPRODUCTS DERIVED FROM BURNED COVERAGE

 📅 DATE: April 2026

 TEAM:
 Fire scar mapping network - MapBiomas Fire
 - Instituto de Pesquisa Ambiental da Amazonia (IPAM)
 - Wallace Silva and Vera Arruda; wallace.silva@ipam.org.br, vera.arruda@ipam.org.br
 -------------------------------------------------------------
 📌 WHAT DOES THIS SCRIPT DO?
 Generates and exports 4 products from the fire scars:

 🟠 `monthlyBurnedCoverage`: month + LULC class encoding.
 🔵 `annualBurnedCoverage`: annual binary + LULC class encoding.
 🟡 `monthlyBurnedArea`: month of occurrence (1 to 12), without LULC.
 🟢 `annualBurnedArea`: annual fire (0/1), without LULC.

 -------------------------------------------------------------
 🔧 WHAT SHOULD I MODIFY TO USE THIS SCRIPT?
 ✅ Select the desired country (selection block).
 ✅ Check `assetOutput`, `bounds` and the output names.

***************************************************************/

// 🔧 Country selection
// 🌍 Country under analysis
var country = 'colombia'; // 'bolivia', 'chile', 'colombia', 'ecuador',  'guyana', 'paraguay', 'peru' , 'venezuela'
var coll_n = '1';
// 🧾 Output file names
var outFileNameMontlhyCoverage = 'mapbiomas_' + country + '_fire_collection'+coll_n+'_monthly_burned_coverage_v1'; 
var outFileNameBurnedCoverage = 'mapbiomas_' + country + '_fire_collection'+coll_n+'_annual_burned_coverage_v1'; 
var outFileNameMontlhy = 'mapbiomas_' + country + '_fire_collection'+coll_n+'_monthly_burned_v1'; 
var outFileNameAnual = 'mapbiomas_' + country + '_fire_collection'+coll_n+'_annual_burned_v1'; 

var assetOutput = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION'+coll_n+'/FINAL_PRODUCTS/';
//var assetOutput = 'projects/mapbiomas-public/assets/' + country + '/fire/collection1/';

// 🧾 Input file names
var assetInput = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION'+coll_n+'/CLASSIFICATION_COLLECTIONS/collection'+coll_n+'_fire_mask_v1';
var scale = 30;

// 🌱 LULC collections by country
var landcoverDict  = {
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

// 🧭 Regions by country
var regionsDict = {
  bolivia: ee.FeatureCollection('projects/mapbiomas-bolivia/assets/FIRE/AUXILIARY_DATA/regiones_fuego_bolivia_v1'),
  chile: ee.FeatureCollection('projects/mapbiomas-chile/assets/FIRE/AUXILIARY_DATA/regiones_fuego_chile_v1'),
  colombia: ee.FeatureCollection('projects/mapbiomas-colombia/assets/FIRE/AUXILIARY_DATA/regiones_fuego_colombia_v1'),
  paraguay: ee.FeatureCollection('projects/mapbiomas-paraguay/assets/FIRE/AUXILIARY_DATA/regiones_fuego_paraguay_v1'),
  peru: ee.FeatureCollection('projects/mapbiomas-peru/assets/FIRE/AUXILIARY_DATA/regiones_fuego_peru_v1')
};

// Active selection:
var landcover = landcoverDict[country];                 // Image
var regions  = regionsDict[country];                    // FeatureCollection
var geometry = regions.union().geometry();              // Country geometry (regions)


// Working years
var startYear = 2000;
var endYear = 2025;
var years = ee.List.sequence(startYear, endYear);

// Active selection
var landcover = landcoverDict[country];
var regions = regionsDict[country];
var geometry = regions.union().geometry().bounds();
Map.addLayer(geometry,{},'region bounds',false)

// Fit the LULC bands to the range and duplicate 2024→2025 if missing
var lc = ee.ImageCollection.fromImages(
  years.map(function(y) {
    y = ee.Number(y).int();
    var band = ee.String('classification_').cat(y.format('%d'));
    var hasBand = landcover.bandNames().contains(band);

    return ee.Image(ee.Algorithms.If(
      hasBand,
      landcover.select([band]),
      landcover.select('classification_2024').rename(band)
    ));
  })
).toBands();

var lcNames = years.map(function(y) {
  y = ee.Number(y).int();
  return ee.String('classification_').cat(y.format('%d'));
});

landcover = lc.rename(lcNames);
print('landcover',landcover)
// 📅 Load the fire scar collection
var fireCollection = ee.ImageCollection(assetInput).map(function(img) {
  var mask = img.neq(13);
  return img.updateMask(mask);
});
print('fireCollection',fireCollection)
var fireCollectionMosaic = fireCollection.mosaic();

// var monthlyBurnedArea = ee.Image(
//   years.iterate(function(year, prev) {
//     year = ee.Number(year).int();
//     var image = fireCollection
//       .filter(ee.Filter.eq('year', year))
//       .mosaic()
//       .rename(ee.String('burned_coverage_').cat(year.format('%d')));
//     return ee.Image(prev).addBands(image);
//   }, ee.Image().select())
// );
var monthlyBurnedArea = fireCollectionMosaic
print('monthlyBurnedArea',monthlyBurnedArea)

// ---------------------------------------------------------
// 🛑 NEW STEP: Mask out unwanted LULC classes (e.g. 31 = Water)
// ---------------------------------------------------------

// List of classes that cannot burn
var classesToRemove = [24,31,75]; 
var zeros = ee.List.repeat(0, classesToRemove.length);

var maskedFireBands = years.map(function(y) {
  var year = ee.Number(y).int();
  
  // Build the band names for the current year
  var fireBandName = ee.String('burned_coverage_').cat(year.format('%d'));
  var lulcBandName = ee.String('classification_').cat(year.format('%d'));
  
  // Select the fire band and the LULC band for that year
  var fireBand = monthlyBurnedArea.select([fireBandName]);
  var lulcBand = landcover.select([lulcBandName]);
  
  // Build the mask: 'remap' turns class 31 into 0 and everything else into 1
  var lulcMask = lulcBand.remap(classesToRemove, zeros, 1);
  
  // Update the fire mask (drops the pixels where the mask is 0)
  return fireBand.updateMask(lulcMask);
});

// Overwrite monthlyBurnedArea with the new clean image
monthlyBurnedArea = ee.ImageCollection.fromImages(maskedFireBands)
  .toBands()
  .rename(monthlyBurnedArea.bandNames()); // Restore the original band names
// ---------------------------------------------------------

// 🟠 Product: Monthly burned coverage (month + class)
var monthlyBurnedCoverage = monthlyBurnedArea
  .multiply(100)
  .add(landcover)
  .uint16();

// 🔵 Product: Annual binary burned coverage (0/1 + class)
var annualBurnedCoverage = monthlyBurnedArea
  .gte(1)
  .multiply(landcover)
  .uint8();

// 🟡 Product: Monthly burned area (month 1–12 only)
var monthlyBandNames = monthlyBurnedArea.bandNames().map(function(bn) {
  return ee.String(bn).replace('burned_coverage_', 'burned_monthly_');
});
var monthlyBurnedAreaClean = monthlyBurnedArea
  .rename(monthlyBandNames)
  .uint8();

// 🟢 Product: Annual binary burned area (0 or 1)
var annualBandNames = monthlyBandNames.map(function(bn) {
  return ee.String(bn).replace('burned_monthly_', 'burned_area_');
});
var annualBurnedArea = monthlyBurnedArea
  .gt(0)
  .rename(annualBandNames)
  .uint8();

// 📤 Export of results

print('🟠 monthlyBurnedCoverage',monthlyBurnedCoverage);

Export.image.toAsset({
  image: monthlyBurnedCoverage,
  description: outFileNameMontlhyCoverage,
  assetId: assetOutput + outFileNameMontlhyCoverage,
  pyramidingPolicy: { '.default': 'mode' },
  region: geometry,
  scale: scale,
  maxPixels: 1e13,
});

print('🔵 annualBurnedCoverage',annualBurnedCoverage);

Export.image.toAsset({
  image: annualBurnedCoverage,
  description: outFileNameBurnedCoverage,
  assetId: assetOutput + outFileNameBurnedCoverage,
  pyramidingPolicy: { '.default': 'mode' },
  region: geometry,
  scale: scale,
  maxPixels: 1e13,
});

print('🟡 monthlyBurnedArea',monthlyBurnedAreaClean);

Export.image.toAsset({
  image: monthlyBurnedAreaClean,
  description: outFileNameMontlhy,
  assetId: assetOutput + outFileNameMontlhy,
  pyramidingPolicy: { '.default': 'mode' },
  region: geometry,
  scale: scale,
  maxPixels: 1e13,
});

print('🟢 annualBurnedArea',annualBurnedArea);

Export.image.toAsset({
  image: annualBurnedArea,
  description: outFileNameAnual,
  assetId: assetOutput + outFileNameAnual,
  pyramidingPolicy: { '.default': 'mode' },
  region: geometry,
  scale: scale,
  maxPixels: 1e13,
});

// 🧪 Visualization (optional)
var year = 2019;
var fire_palettes = require('users/geomapeamentoipam/MapBiomas__Fogo:00_Tools/Palettes.js');
var lulc_palettes = require('users/kahuertas/mapbiomas-colombia:mapbiomas-colombia/collection-4/modules/Palettes.js').get('colombiaCol4');
Map.addLayer(
  monthlyBurnedCoverage.mod(100).byte(),
  {
    min: 0,
    max: lulc_palettes.length-1,
    bands: 'burned_coverage_' + year,
    palette: lulc_palettes
  },
  '🟠 Cobertura quemada mensual- uso del suelo ' + year
);


Map.addLayer(
  annualBurnedCoverage,
  {
    min: 0,
    max: lulc_palettes.length-1,
    bands: 'burned_coverage_' + year,
    palette: lulc_palettes
  },
  '🔵 Cobertura quemada anual - uso del suelo ' + year
);

Map.addLayer(
  monthlyBurnedAreaClean,
  {
    min: 1,
    max: 12,
    bands: 'burned_monthly_' + year,
    palette: [
        "#cc00ff",
        "#6600ff",
        "#0000ff",
        "#00ccff",
        "#00ffcc",
        "#ffff00",
        "#ff9900",
        "#ff3300",
        "#cc0000",
        "#00cc00",
        "#009900",
        "#66ff66",
    ]
  },
  '🟡 Área quemada mensual - mes de ocurrencia ' + year
);

Map.addLayer(
  annualBurnedArea,
  {
    bands: 'burned_area_' + year,
    palette: '800000'
  },
  '🟢 Área quemada anual - presencia binaria ' + year
);
