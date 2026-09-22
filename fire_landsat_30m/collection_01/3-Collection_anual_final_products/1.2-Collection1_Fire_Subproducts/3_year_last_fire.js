/***************************************************************
 MAPBIOMAS FIRE - SUBPRODUCT: YEAR LAST FIRE (2013-2024)

 📅 DATE: April 20, 2026

 TEAM:
 Fire scar mapping network - MapBiomas Fire
 - Instituto de Pesquisa Ambiental da Amazonia (IPAM)
 - Wallace Silva and Vera Arruda; wallace.silva@ipam.org.br, vera.arruda@ipam.org.br
 -------------------------------------------------------------
 📌 WHAT DOES THIS SCRIPT DO?
 Computes and exports the subproduct:

 🔴 `year_last_fire`: for each year Y, the value is the year of the last fire that
    occurred up to Y (inclusive).

 🧠 Logic (summary):
 The script uses an iterative, accumulative method. For each year in the
 series it compares against the previous year's image. If there is fire in the
 current year, the pixel value is updated to the current year; otherwise the
 pixel keeps the previous year's value. This guarantees that, for each year,
 the resulting image holds the year of the last fire up to that moment.

 -------------------------------------------------------------
 🔧 WHAT SHOULD I MODIFY TO USE THIS SCRIPT?
 ✅ Check `assetInput` (scar collection).
 ✅ Check `assetOutput` and `outFileName`.

 ***************************************************************/

/* ===========================
 🔧 CONFIGURATION BLOCK
 =========================== */

var country = 'colombia';// 'bolivia', 'chile', 'colombia', 'ecuador',  'guyana', 'paraguay', 'peru' , 'venezuela'
var coll_n = '1';

// 🔥 Scar collection 
var assetInput  = 'projects/mapbiomas-'+country+'/assets/FIRE/COLLECTION'+coll_n+'/FINAL_PRODUCTS/mapbiomas_'+country+'_fire_collection'+coll_n+'_annual_burned_v1';

// 📤 Output folder and name of the resulting asset
var assetOutput = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION1/FINAL_PRODUCTS/';
var outFileName = 'mapbiomas_'+country+'_fire_collection'+coll_n+'_year_last_fire_v1';

// ⚙️ Spatial resolution
var scale = 30;

/* ===========================================
 📥 INPUT LOADING AND PREPARATION
 =========================================== */

// Load the multiband image with one band per year
// Band names: burned_area_YYYY
var scars = ee.Image(assetInput);

/* ===================================================
 🧠 ACCUMULATIVE COMPUTATION OF THE YEAR LAST FIRE (YLF)
 ===================================================

 Idea:
 - Iterate over the band names of the scar image.
 - Use an initial "seed" image for the first step.
 - On each iteration, compare the current year's band with the last band
   of the accumulated image. If the pixel burned, it is updated with the
   current year; otherwise it keeps the previous value.
 */

// Seed: empty "previous" band (year startYear-1) to simplify the iteration
// Iterator that applies the accumulative logic
function year_last_fire_iteration(current, previous) {
  var bandName = ee.String(current);
  var year_current = ee.Number.parse(bandName.slice(-4)).int();
  var prevImg = ee.Image(previous);

  // The band to add is the last one of 'previous'
  // replacing the burned pixels with the current year
  var this_year = prevImg.slice(-1).where(scars.select(bandName), year_current)
    .rename(ee.String('year_last_fire_').cat(year_current.add(1)));

  // Return the accumulated image with the new band
  return prevImg.addBands(this_year);
}

// Iterate over every year and drop the seed band
var year_last_fire_final = ee.Image(
  scars.bandNames().iterate(year_last_fire_iteration, ee.Image(0))
).slice(1).selfMask()
.int16();

print('🔴 year_last_fire (1999–2025)', year_last_fire_final);

// Export geometry
var geometry = scars.geometry();

/* ===========================
 📤 EXPORT OF RESULTS
 =========================== */

Export.image.toAsset({
  image: year_last_fire_final.uint16(),
  description: outFileName,
  assetId: assetOutput + outFileName,
  pyramidingPolicy: { '.default': 'mode' },
  region: geometry,
  overwrite:true,
  scale: scale,
  maxPixels: 1e13
});

/* ===========================
 🧪 VISUALIZATION (OPTIONAL)
 =========================== */

var palette = [
    '#010b7b','#01177c','#00227e','#002d80','#004284','#004c86',
    '#00608a','#026b8d','#008998','#26a09e','#5db9ad','#94d2bd',
    '#9fbd98','#aaa772','#bf7c27','#c45905','#b93c0c','#b42e0f',
    '#ae2012','#aa2016','#a6211a','#a3211e','#9b2226','#901417',
    '#810000','#810000'//,'#750000'
];

// Visualize a specific YLF year (e.g. 2025)
var year_vis = 2025;
var visParams = {
  bands: ['year_last_fire_' + year_vis],
  min: 2000,
  max: 2025,
  palette: palette
};

Map.addLayer(year_last_fire_final, visParams, '🔴 Year Last Fire ' + year_vis);
Map.addLayer(scars, {min:0, max:1, palette:['000000','FF0000'],bands:'burned_area_' + year_vis}, '🔥 Fuego ' + year_vis, false);
Map.centerObject(center, 5);

/* ===========================================
 📊 LEGEND GENERATION (3-COLUMN STYLE)
 =========================================== */

// 1. Create the main legend panel
var legend = ui.Panel({
  style: {
    position: 'bottom-right',
    padding: '8px 15px',
    backgroundColor: 'rgba(33, 33, 33, 0.9)', 
    border: '1px solid #555'                  
  }
});

// 2. Add the title
legend.add(ui.Label({
  value: 'Año del último fuego',
  style: {
    fontWeight: 'bold',
    fontSize: '13px',
    margin: '0 0 8px 0',
    color: '#ffffff',
    backgroundColor: 'rgba(0,0,0,0)'
  }
}));

// Year range to display
var startYear = 2000;
var endYear = 2025;

// 3. Create the main horizontal container for the columns
var columnsContainer = ui.Panel({
  layout: ui.Panel.Layout.Flow('horizontal'),
  style: { backgroundColor: 'rgba(0,0,0,0)' }
});

// 4. Create 3 vertical sub-panels (the columns)
var col1 = ui.Panel({ layout: ui.Panel.Layout.Flow('vertical'), style: { backgroundColor: 'rgba(0,0,0,0)' } });
var col2 = ui.Panel({ layout: ui.Panel.Layout.Flow('vertical'), style: { backgroundColor: 'rgba(0,0,0,0)' } });
var col3 = ui.Panel({ layout: ui.Panel.Layout.Flow('vertical'), style: { backgroundColor: 'rgba(0,0,0,0)' } });

// Group the columns in an array to simplify the iteration
var columns = [col1, col2, col3];

// Add the columns to the horizontal container
columnsContainer.add(col1);
columnsContainer.add(col2);
columnsContainer.add(col3);

// Add the column container to the main legend
legend.add(columnsContainer);

// 5. Iterate over the years and spread them across the 3 columns
for (var i = 0; i <= (endYear - startYear); i++) {
  var currentYear = startYear + i;
  var currentColor = palette[i]; // Uses the palette defined at line 99

  // Color box
  var colorBox = ui.Label({
    style: {
      backgroundColor: currentColor,
      padding: '7px',
      margin: '0 8px 0 0',
      border: '1px solid #444'
    }
  });

  // Year text
  var description = ui.Label({
    value: currentYear.toString(),
    style: {
      margin: '0',
      color: '#dddddd',
      backgroundColor: 'rgba(0,0,0,0)',
      fontSize: '11px'
    }
  });

  // Single row (box + text)
  var itemRow = ui.Panel({
    widgets: [colorBox, description],
    layout: ui.Panel.Layout.Flow('horizontal'),
    style: { 
      margin: '2px 15px 2px 0', // Extra right margin to separate the columns
      backgroundColor: 'rgba(0,0,0,0)' 
    } 
  });

  // Decide which column this year goes to using the modulo operator (%)
  // This spreads them left to right, row by row.
  var colIndex = i % 3; 
  columns[colIndex].add(itemRow);
}

Map.add(legend);