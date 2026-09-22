/* MAPBIOMAS FIRE - COLLECTION 1 - COLOMBIA (REFERENCE)
 Application of the land use and land cover (LULC) masks
 over the fire scars

 📅 DATE: May 2026

 TEAM:
 Fire scar mapping network - MapBiomas Fire
 - Instituto de Pesquisa Ambiental da Amazonia (IPAM)
 - Wallace Silva and Vera Laisa

 🔗 REFERENCES:
 https://code.earthengine.google.com/4125bda1034925e6261d134e3c89a574
 https://code.earthengine.google.com/5c46e4b2a62b48349f01d35ac4cf063a

 -------------------------------------------------------------
 📌 WHAT DOES THIS SCRIPT DO?
 1. Loads the fire scars without a mask (no-mask collection).
 2. Applies the land cover masks (MapBiomas) by region and year.
 3. Removes isolated pixels and builds a final version of the image.
 4. Exports the masked images with month encoding (Landsat NBR).

 -------------------------------------------------------------
 🔧 WHAT SHOULD I MODIFY TO USE THIS SCRIPT?
 ✅ Change `landcover` and `region` according to the country.
 ✅ Check the input (`col_nomask_id`) and output (`col_mask_id`) paths.
 ✅ Use the filters at the end of the script to pick the year and the region
    before exporting (avoids exporting everything at once).
 -------------------------------------------------------------
 ⚠️ IMPORTANT RECOMMENDATION:
 ✅ Use the filters above to select only one region or one year at a time.
 This helps avoid task overload and keeps the process under control.
 📌 Each exported image generates an **individual task ("Task")**
 that shows up in the **"Tasks"** tab of the Earth Engine Code Editor.
 ▶️ You must click **"Run"** and then confirm manually
 for each task before it starts.
       🧩 SUGGESTION:
       You can install the **Open Earth Engine** browser extension (for Google Chrome),
       which adds a **"Run All Tasks"** button to launch every pending task
       at once, much faster.
***************************************************************/

var country = 'colombia';
var coll_n = '1';
var years_vis = [2000, 2021, 2022, 2023];
var years_process = [
      2000, 2001, 2002, 2003,
      2004, 2005, 2006, 2007, 
      2008, 2009, 2010, 2011, 
      2012, 2013, 2014, 2015,
      2016, 2017, 2018, 2019,
      2020, 2021, 2022, 2023,
      2024, 2025
    ]

// E.g.: periodo = "2023,2025" -> this geometry only masks
// fire between 2023 and 2025 (inclusive)
var geometry_no_fire = [
                          from_2023_to2025, 
                          from_2020_to2020, 
                          from_2020_to2021
                          
                          ]

// 🎯 Custom masks by region (adjust for each country)
// Legend codes: https://paraguay.mapbiomas.org/en/codigos-de-la-leyenda
// 9: Forestry
// 22: Area without vegetation
// 26: Water body
var masks = {
'r01': [26,22],
// 'r02': [26,22],
// 'r03': [26,22],
// 'r04': [26,22],
// 'r05': [26,22],
// 'r06': [26,22],
// 'r07': [26,22],
'r08': [13,26,22,68],
// 'r09': [26,22],
// 'r10': [26,22],
// 'r11': [26,22],
// 'r12': [26,22],
// 'r13': [26,22],
// 'r14': [26,22],
// 'r15': [26,22],
// 'r16': [26,22],
// 'r17': [26,22],
// 'r18': [26,22],
// 'r19': [26,22],
// 'r20': [26,22],
// 'r21': [26,22],
// 'r22': [26,22],
// 'r23': [26,22],
// 'r24': [26,22],
// 'r25': [26,22],
// 'r26': [26,22],
// 'r27': [26,22],
// 'r28': [26,22],
// 'r29': [26,22],
// 'r30': [26,22],
// 'r31': [26,22],
// 'r32': [26,22],
// 'r33': [26,22],
// 'r34': [26,22],
// 'r35': [26,22],
// 'r36': [26,22],
// 'r37': [26,22],
// 'r38': [26,22],
// 'r39': [26,22],
// 'r40': [26,22],
// 'r41': [26,22],
// 'r42': [26,22],
// 'r43': [26,22],
// 'r44': [26,22],
// 'r45': [26,22],
// 'r46': [26,22],
// 'r47': [26,22],
// 'r48': [26,22],
// 'r49': [26,22],
// 'r50': [26,22],
// 'r51': [26,22],
// 'r52': [26,22],
// 'r53': [26,22],
// 'r54': [26,22],
// 'r55': [26,22],
// 'r56': [26,22],
// 'r57': [26,22],
// 'r58': [26,22],
// 'r59': [26,22],
// 'r60': [26,22],
// 'r61': [26,22],
// 'r62': [26,22],
// 'r63': [26,22],
// 'r64': [26,22],
// 'r65': [26,22],
// 'r66': [26,22],
// 'r67': [26,22],
// 'r68': [26,22],
// 'r69': [26,22],
// 'r70': [26,22],
// 'r71': [26,22],
// 'r72': [26,22],
// 'r73': [26,22],
// 'r74': [26,22],
// 'r75': [26,22],
// 'r76': [26,22],
// 'r77': [26,22],
// 'r78': [26,22],
// 'r79': [26,22],
// 'r80': [26,22],
// 'r81': [26,22],
// 'r82': [26,22],
// 'r83': [26,22],
// 'r84': [26,22],
// 'r85': [26,22],
// 'r86': [26,22],
// 'r87': [26,22],
// 'r88': [26,22],
// 'r89': [26,22],
// 'r90': [26,22],
// 'r91': [26,22],
// 'r92': [26,22],
// 'r93': [26,22],
// 'r94': [26,22],
// 'r95': [26,22],
// 'r96': [26,22],
// 'r97': [26,22],
// 'r98': [26,22],
};



///// DO NOT MODIFY FROM THIS SECTION ON


// 🧯 Parse the period (year range) of each "no fire" geometry
// Same convention as 'periodo' in the General Map Filter script:
// periodo = "StartYear,EndYear" -> FIni / FFin inclusive
var noFireAtrib = geometry_no_fire.map(function(geo) {
  var props = geo.getInfo().features[0].properties;
  var periodo = props.periodo.split(',');
  return {
    geometry: geo,
    FIni: parseInt(periodo[0]),
    FFin: parseInt(periodo[1])
  };
});

print('noFireAtrib', noFireAtrib);

// 🧯 Builds, for a given year, the combined mask of the "no fire"
// geometries whose period covers that year. Returns null if none applies.
function getNoFireMaskForYear(year) {
  var activeGeoms = noFireAtrib.filter(function(item) {
    return item.FIni <= year && year <= item.FFin;
  });

  if (activeGeoms.length === 0) {
    return null;
  }

  // Each item.geometry is already a drawn FeatureCollection/Feature.
  // Instead of rebuilding a MultiPolygon from coordinates (which
  // fails when the geometry is a GeometryCollection with several polygons),
  // we simply merge the active FeatureCollections and use their
  // combined geometry directly, just like geo.geometry() in the reference
  // script (General Map Filter).
  var mergedFeatures = ee.FeatureCollection(
    activeGeoms.map(function(item) {
      return ee.FeatureCollection(item.geometry);
    })
  ).flatten();

  var combinedGeometry = mergedFeatures.geometry();

  return ee.Image.constant(1)
    .clip(combinedGeometry)
    .mask()
    .not(); // 1 outside the active geometries, 0 inside any of them
}

// section to exclude the spatial filter
var mask_SpacialFilter = ee.Image.constant(1)
    .clip(no_spacial_filter)

var years_burned = years_process.map(function(year){
  return 'burned_coverage_'+year
})

// 🗂️ Input collection (without mask)
var col_nomask_id = 'projects/mapbiomas-'  + country + '/assets/FIRE/COLLECTION'+coll_n+'/CLASSIFICATION_COLLECTIONS/collection'+coll_n+'_fire_no_mask_v1';
var col_mask_id = 'projects/mapbiomas-'  + country + '/assets/FIRE/COLLECTION'+coll_n+'/CLASSIFICATION_COLLECTIONS/collection'+coll_n+'_fire_mask_v1';

print('col_nomask_id', col_nomask_id, ee.ImageCollection(col_nomask_id).limit(10));


var landcovers = {
  bolivia: ee.Image('projects/mapbiomas-public/assets/bolivia/lulc/collection3/mapbiomas_bolivia_collection3_integration_v1'),
  chile: ee.Image('projects/mapbiomas-public/assets/chile/lulc/collection2/mapbiomas_chile_collection2_coverage_v2'),
  colombia: ee.Image('projects/mapbiomas-colombia/assets/LULC/COLECCION3/INTEGRACION/COLOMBIA-1'),
  paraguay: ee.Image('projects/mapbiomas-public/assets/paraguay/collection2/mapbiomas_paraguay_collection2_integration_v1'),
  peru: ee.Image('projects/mapbiomas-public/assets/peru/collection2/mapbiomas_peru_collection2_integration_v1'),
};

var regionss = {
  bolivia: ee.FeatureCollection('projects/mapbiomas-bolivia/assets/FIRE/AUXILIARY_DATA/regiones_fuego_bolivia_v1'),
  chile: ee.FeatureCollection('projects/mapbiomas-chile/assets/FIRE/AUXILIARY_DATA/regiones_fuego_chile_v1'),
  colombia: ee.FeatureCollection('projects/mapbiomas-colombia/assets/FIRE/AUXILIARY_DATA/regiones_fuego_colombia_v1'),
  paraguay: ee.FeatureCollection('projects/mapbiomas-paraguay/assets/FIRE/AUXILIARY_DATA/regiones_fuego_paraguay_v1'),
  peru: ee.FeatureCollection('projects/mapbiomas-peru/assets/FIRE/AUXILIARY_DATA/regiones_fuego_peru_v1'),
};

var mosaics_fuego =  'projects/mapbiomas-mosaics/assets/LANDSAT/FIRE/mosaics-countries'
var leyendaPanel = require(
'users/kahuertas/mapbiomas-colombia:mapbiomas-colombia/collection-4/utils/leyendaC4'
);

var palette_lulc = require(
'users/kahuertas/mapbiomas-colombia:mapbiomas-colombia/collection-4/modules/Palettes.js'
).get('colombiaCol4');

var fire_vis_m = {
    min: 1,
    max: 13,
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
        "#807E7E"
    ]
};

var mosaic_green = { bands: ['swir1', 'nir', 'red'], min: 0.03, max: 0.4, gamma: 1}
// 1. Get distinct regions and distinct years
var regions = ee.List([
    'r01', 'r02', 'r03', 'r04', 'r05', 'r06', 'r07', 'r08', 'r09', 'r10',
    'r11', 'r12', 'r13', 'r14', 'r15', 'r16', 'r17', 'r18', 'r19', 'r20',
    'r21', 'r22', 'r23', 'r24', 'r25', 'r26', 'r27', 'r28', 'r29', 'r30',
    'r31', 'r32', 'r33', 'r34', 'r35', 'r36', 'r37', 'r38', 'r39', 'r40',
    'r41', 'r42', 'r43', 'r44', 'r45', 'r46', 'r47', 'r48', 'r49', 'r50',
    'r51', 'r52', 'r53', 'r54', 'r55', 'r56', 'r57', 'r58', 'r59', 'r60',
    'r61', 'r62', 'r63', 'r64', 'r65', 'r66', 'r67', 'r68', 'r69', 'r70',
    'r71', 'r72', 'r73', 'r74', 'r75', 'r76', 'r77', 'r78', 'r79', 'r80',
    'r81', 'r82', 'r83', 'r84', 'r85', 'r86', 'r87', 'r88', 'r89', 'r90',
    'r91', 'r92', 'r93', 'r94', 'r95', 'r96', 'r97', 'r98'
])
var years = ee.List(years_process)

// 2. Map over each region
col_nomask_id = ee.ImageCollection(col_nomask_id)
var regionMosaics = ee.ImageCollection.fromImages(regions.map(function(region) {
  
  var regionCol = col_nomask_id.filter(ee.Filter.eq('region', region));
  
  var annualMosaics = ee.ImageCollection.fromImages(years.map(function(year) {
    var yStr = ee.Number(year).format('%d');
    var yearCol = regionCol.filter(ee.Filter.eq('year', year));
    var bandName = ee.String('burned_coverage_').cat(yStr);
    
    var mosaic = ee.Image(ee.Algorithms.If(
      yearCol.size().gt(0),
      yearCol.mosaic().rename([bandName]),
      ee.Image.constant(0).selfMask().rename([bandName])
    ));
    
    return mosaic.set('system:index', yStr);
  }));

  var singleImageRaw = annualMosaics.toBands();
  
  var finalBandNames = singleImageRaw.bandNames().map(function(name) {
    return ee.String(name).replace('^[0-9]{4}_', '');
  });
  
  return singleImageRaw.rename(finalBandNames)
                       .set('region', region)
                       .set('system:index', ee.String(region));
}));




var landcover = landcovers[country];
var region = regionss[country];

var geometry = landcover.geometry()

landcover = landcover.addBands(
  landcover.select('classification_2024').rename('classification_2025')
);
print('landcover',landcover)



var collection_landsat = require('users/geomapeamentoipam/MapBiomas__Fogo:00_Tools/require-landsat-collection');




function exportImageMultiBand(regionImage, id_region) {
  var mask_classes = masks[id_region];

  var processedBandsArray = [];

  years_process.forEach(function(y) {
    var yearStr = y.toString();
    var bandName = 'burned_coverage_' + yearStr;

    var fireYear = regionImage.select(bandName);

    var landcover_mask = landcover
      .select('classification_' + yearStr)
      .eq(mask_classes)
      .reduce('sum')
      .gte(1);

    var final_mask = landcover_mask.neq(1);
    var image_mask = fireYear.updateMask(final_mask);

    var connections = image_mask.connectedPixelCount({'maxSize': 100, 'eightConnected': false});
    
    var solitary_pixels = connections.lte(4)
                              .where(mask_SpacialFilter,0);
                              
    image_mask = image_mask.where(solitary_pixels, 0).selfMask().reproject('EPSG:4326', null, 30);

    var qualityMosaic = collection_landsat.landsat_year(y, geometry)
      .qualityMosaic('nbr')
      .select('monthOfYear')
      .byte();

    var finalYearBand = qualityMosaic.updateMask(image_mask).rename([bandName]);
    
    // remap filter (only applies to the years covered by 'periodo')
    var noFireMaskYear = getNoFireMaskForYear(y);
    if (noFireMaskYear !== null) {
      // finalYearBand = finalYearBand.updateMask(noFireMaskYear);
      finalYearBand = finalYearBand.where(noFireMaskYear.eq(0), 13);
    }
    
    if (years_vis.indexOf(y) !== -1) {
      // Map.addLayer(mosaic_fuego_year, mosaic_green, 'Mosaic_Green ' + yearStr, false)
      
      Map.addLayer(
        landcover.clip(region_).select('classification_' + yearStr),
        {
          min: 0,
          max: palette_lulc.length-1,
          palette: palette_lulc
        },
        'Cobertura del suelo ' + yearStr,
        false
      );
      Map.addLayer(fireYear, {min: 0, max: 1, palette: ['A82000']},
        'Fuego sin máscara ' + id_region + '-' + yearStr, false);

      Map.addLayer(finalYearBand, fire_vis_m,
        'Imagen final (meses) ' + id_region + '-' + yearStr, false);
    }

    processedBandsArray.push(finalYearBand);
  });

  var multiBandFinal = ee.ImageCollection.fromImages(processedBandsArray).toBands();

  var cleanBandNames = multiBandFinal.bandNames().map(function(name) {
    return ee.String(name).replace('^[0-9]+_', '');
  });

  multiBandFinal = multiBandFinal.rename(cleanBandNames).set({
    'source': 'mapbiomas-fuego',
    'pixel_unit': 'month',
    'region': id_region
  });

  var regionPadded = id_region.replace('r', 'region');
  var exportName = 'burned_area_' + country + '_' + regionPadded;
  

  Export.image.toAsset({
    image: multiBandFinal,
    description: exportName,
    assetId: col_mask_id + '/' + exportName,
    pyramidingPolicy: 'mode',
    region: geometry,
    scale: 30,
    maxPixels: 1e13,
    overwrite: true
  });
}

var id_regions = Object.keys(masks);
var id_regions_num = id_regions.map(function(r) {
  return parseInt(r.replace('r', ''), 10);
});
var region_ = region.filter(ee.Filter.inList('region',id_regions_num))

/// view mosaics 
years_vis.forEach(function(y) {
  var yearStr = y.toString();
  
  // 1. Fetch and load the MapBiomas Fire Mosaic
  var mosaic_fuego_year = getMosaicMBFire(y)
                              .clip(region_);
  Map.addLayer(mosaic_fuego_year, mosaic_green, 'Mosaic_Green ' + yearStr, false);
  
});


Map.addLayer(region_.style({fillColor:'00000000',color:'red'}),{},'regiones procesar')
print('id_regions',id_regions)
print('regionMosaics',regionMosaics)
id_regions.forEach(function(id_region){
  
  var regionStr = id_region.toString();

  var single_img = regionMosaics
                      .filter(ee.Filter.eq('region',id_region))
                      .mosaic()
                      .select(years_burned)
                      
  exportImageMultiBand(single_img, regionStr)
  
})


/**
 * LEGEND
 */
var legend = ui.Panel({
    style: {
        position: 'bottom-right',
        padding: '8px 15px',
        backgroundColor: 'rgba(33, 33, 33, 0.9)',
        border: '1px solid #555'
    }
});

var legendTitle = ui.Label({
    value: 'Mes de Quema',
    style: {
        fontWeight: 'bold',
        fontSize: '13px',
        margin: '0 0 8px 0',
        color: '#ffffff',
        backgroundColor: '00000000'
    }
});

legend.add(legendTitle);

var months = [
    'Enero', 
    'Febrero', 
    'Marzo', 
    'Abril', 
    'Mayo', 
    'Junio', 
    'Julio', 
    'Agosto', 
    'Septiembre', 
    'Octubre', 
    'Noviembre', 
    'Diciembre',
    'remap_noFire'
];

var legendColors = getInterpolatedPalette(fire_vis_m.palette, 13);

months.forEach(function(month, i) {
    var colorBox = ui.Label({
        style: {
            backgroundColor: legendColors[i],
            padding: '7px',
            margin: '0 8px 0 0',
            border: '1px solid #444'
        }
    });
    
    var description = ui.Label({
        value: month,
        style: {
            margin: '0',
            color: '#dddddd',
            backgroundColor: '00000000',
            fontSize: '11px'
        }
    });
    
    var row = ui.Panel({
        widgets: [
            colorBox, 
            description
        ],
        layout: ui.Panel.Layout.Flow('horizontal'),
        style: {
            margin: '2px 0',
            backgroundColor: '00000000'
        }
    });
    
    legend.add(row);  
});

Map.add(legend);

/**
 * DARK MODE MAP CONFIGURATION
 */
var dark_style = [
  {
    "elementType": "geometry",
    "stylers": [
      {
        "color": "#212121"
      }
    ]
  },
  {
    "elementType": "labels.icon",
    "stylers": [
      {
        "visibility": "off"
      }
    ]
  },
  {
    "elementType": "labels.text.fill",
    "stylers": [
      {
        "color": "#757575"
      }
    ]
  },
  {
    "elementType": "labels.text.stroke",
    "stylers": [
      {
        "color": "#212121"
      }
    ]
  },
  {
    "featureType": "administrative",
    "elementType": "geometry",
    "stylers": [
      {
        "color": "#757575"
      }
    ]
  },
  {
    "featureType": "administrative.country",
    "elementType": "labels.text.fill",
    "stylers": [
      {
        "color": "#9e9e9e"
      }
    ]
  },
  {
    "featureType": "water",
    "elementType": "geometry",
    "stylers": [
      {
        "color": "#000000"
      }
    ]
  },
  {
    "featureType": "water",
    "elementType": "labels.text.fill",
    "stylers": [
      {
        "color": "#3d3d3d"
      }
    ]
  }
];

Map.setOptions('Dark', { 'Dark': dark_style });

/**
 * Helper functions
 */
function hexToRgb(hex) {
    
    var shorthandRegex = /^#?([a-f\d])([a-f\d])([a-f\d])$/i;
    
    hex = hex.replace(
        shorthandRegex, 
        function(m, r, g, b) {return r + r + g + g + b + b}
    );
    
    var result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    
    return result
       ? [ parseInt(result[1], 16), parseInt(result[2], 16), parseInt(result[3], 16) ] 
       : null;
      
}

function rgbToHex(r, g, b) {

    return "#" + ((1 << 24) + (r << 16) + (g << 8) + b)
        .toString(16)
        .slice(1);
        
}

function getInterpolatedPalette(palette, steps) {
    
    var rgbs = palette.map(hexToRgb);
    
    var interpolated = [];
    
    for (var i = 0; i < steps; i++) {
    
        var t = i / (steps - 1);
    
        var scaledT = t * (palette.length - 1);
    
        var idx1 = Math.floor(scaledT);
    
        var idx2 = Math.min(idx1 + 1, palette.length - 1);
    
        var weight = scaledT - idx1;
        
        var r = Math.round(rgbs[idx1][0] * (1 - weight) + rgbs[idx2][0] * weight);
    
        var g = Math.round(rgbs[idx1][1] * (1 - weight) + rgbs[idx2][1] * weight);
    
        var b = Math.round(rgbs[idx1][2] * (1 - weight) + rgbs[idx2][2] * weight);
        
        interpolated.push(rgbToHex(r, g, b));
        
    }
    
    return interpolated;
    
}


function getMosaicMBFire(year) {
  var mosaic = ee.ImageCollection(mosaics_fuego)
    .filter(ee.Filter.eq('year', year))
    .filter(ee.Filter.eq('country_id', 'Colombia'))
    .first();
  
  // Error handling when there is no mosaic for that year
  if (!mosaic) return ee.Image().paint(ee.FeatureCollection([ee.Feature(ee.Geometry.Point([0,0]))]), 0);
    
  mosaic = mosaic.divide(100).float();
  var nbr = mosaic.expression(
    '(NIR - SWIR1) / (NIR + SWIR1)', {
      'NIR': mosaic.select('nir'),
      'SWIR1': mosaic.select('swir1')
  }).rename('nbr');
  return mosaic.addBands(nbr);
}

