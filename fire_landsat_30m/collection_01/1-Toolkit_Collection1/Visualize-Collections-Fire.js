/***************************************
 * * MapBiomas Fire Collection by country
 ***************************************/
 //stable: https://code.earthengine.google.com/59ec5b722dbe38e8018be0057e571ee2

// ========================= BASIC CONFIG =========================

// Supported countries
var COUNTRY = {
  ARGENTINA: 'argentina', BOLIVIA: 'bolivia', CHILE: 'chile',
  COLOMBIA: 'colombia', PARAGUAY: 'paraguay', PERU: 'peru'
};

// Initial country
var selectedCountry=COUNTRY.COLOMBIA;

// Configuration by country

var CFG={

  argentina:{
    name:'Argentina',
    gaul:'Argentina',
    project:'mapbiomas-argentina',
    version:1,          // _vN suffix of the final products
    years:[1999,2025], // 27 years
    freq:'frecuencia27',
    last:'ultimo_fuego_27',
    legend:'argentina',
    products:{
      annual:1,annual_cov:1,monthly:1,freq:1,
      acc:1,acc_cov:1,scar_size:1,last_fire:1
    }
  },

  bolivia:{
    name:'Bolivia',
    gaul:'Bolivia',
    project:'mapbiomas-bolivia',
    version:1,          // _vN suffix of the final products
    years:[1995,2025], // 31 years
    freq:'frecuencia31',
    last:'ultimo_fuego_31',
    legend:'bolivia',
    products:{
      annual:1,annual_cov:1,monthly:1,freq:1,
      acc:1,acc_cov:1,scar_size:1,last_fire:1
    }
  },

  chile:{
    name:'Chile',
    gaul:'Chile',
    project:'mapbiomas-chile',
    version:1,          // _vN suffix of the final products
    years:[2013,2025], // 13 years
    freq:'frecuencia13',
    last:'ultimo_fuego_13',
    legend:'chile',
    products:{
      annual:1,annual_cov:1,monthly:1,freq:1,
      acc:1,acc_cov:1,scar_size:1,last_fire:1
    }
  },

  colombia:{
    name:'Colombia',
    gaul:'Colombia',
    project:'mapbiomas-colombia',
    version:3,          // _vN suffix of the final products
    years:[2000,2025], // 26 years
    freq:'frecuencia26',
    last:'ultimo_fuego_26',
    legend:'colombia',
    products:{
      annual:1,annual_cov:1,monthly:1,freq:1,
      acc:1,acc_cov:1,scar_size:1,last_fire:1
    }
  },

  paraguay:{
    name:'Paraguay',
    gaul:'Paraguay',
    project:'mapbiomas-paraguay',
    version:1,          // _vN suffix of the final products
    years:[1999,2024], // 26 years
    freq:'frecuencia26',
    last:null,
    legend:'paraguay',
    products:{
      annual:1,annual_cov:1,monthly:1,freq:1,
      acc:1,acc_cov:1,scar_size:0,last_fire:0
    }
  },

  peru:{
    name:'Peru',
    gaul:'Peru',
    project:'mapbiomas-peru',
    version:1,          // _vN suffix of the final products
    years:[2013,2024], // 12 years
    freq:'frecuencia12',
    last:'ultimo_fuego_12',
    legend:'peru',
    products:{
      annual:1,annual_cov:1,monthly:1,freq:1,
      acc:1,acc_cov:1,scar_size:1,last_fire:1
    }
  }

};

// Palettes
var palettes=require('users/mapbiomasworkspace1/mapbiomas-fire:00_Tools/Palettes.js');
var PAL_CLASS=require('users/mapbiomasworkspace1/mapbiomas-fire:00_Tools/Palettes.js').get('lulc');
var PAL_MENSAL=palettes.get('mensual');
var PAL_TAMANHO=palettes.get('tamanho_n2');
var FREQUENCY_PALETTE={
  frecuencia12:palettes.get('frecuencia12'),
  frecuencia13:palettes.get('frecuencia13'),
  frecuencia26:palettes.get('frecuencia26'),
  frecuencia27:palettes.get('frecuencia27'),
  frecuencia31:palettes.get('frecuencia31')
};
var LAST_FIRE_PALETTE={
  ultimo_fuego_12:palettes.get('ultimo_fuego_12'),
  ultimo_fuego_13:palettes.get('ultimo_fuego_13'),
  ultimo_fuego_26:palettes.get('ultimo_fuego_26'),
  ultimo_fuego_27:palettes.get('ultimo_fuego_27'),
  ultimo_fuego_31:palettes.get('ultimo_fuego_31')
};

// LAND USE AND COVER LEGENDS
var legends = require('users/mapbiomasworkspace1/mapbiomas-fire:00_Tools/Legends.js');

// Dynamic legend for the selected country
function countryLegend(country) {return legends.get('lulc_' + country + '_nivel3');}



// Products (no suffix: the version is resolved per country)
var ASSET_NAME={
  annual:'annual_burned',
  annual_cov:'annual_burned_coverage',
  monthly:'monthly_burned',
  freq:'frequency_burned',
  acc:'accumulated_burned',
  acc_cov:'accumulated_burned_coverage',
  scar_size:'annual_burned_scar_size_range',
  last_fire:'year_last_fire'
};


// Configuration accessors
function cfg(c){return CFG[c];}
function minYear(c){return cfg(c).years[0];}
function maxYear(c){return cfg(c).years[1];}
function totalYears(c){return maxYear(c)-minYear(c)+1;}
function hasProduct(c,k){return !!cfg(c).products[k];}
// Product version: cfg.versions[k] wins over cfg.version, 1 by default
function productVersion(c,k){
  var conf=cfg(c);
  if(conf.versions&&conf.versions[k]!==undefined)return conf.versions[k];
  return conf.version||1;
}
function countryLegend(c){return legends.get('lulc_' + c + '_nivel3');}
function frequencyPalette(c){return FREQUENCY_PALETTE[cfg(c).freq];}
function lastFirePalette(c){
  return LAST_FIRE_PALETTE[cfg(c).last]||null;
}

// Asset path
function assetPath(c,k){
  var collectionFolder = (c === COUNTRY.ARGENTINA)
    ? 'COLLECTION-1' : 'COLLECTION1';

  return 'projects/'+cfg(c).project+'/assets/FIRE/'+collectionFolder+
    '/FINAL_PRODUCTS/mapbiomas_'+c+'_fire_collection1_'+ASSET_NAME[k]+
    '_v'+productVersion(c,k);
}

// Image loading
function loadImages(c){
  var imgs={};
  Object.keys(ASSET_NAME).forEach(function(k){
    if(hasProduct(c,k))imgs[k]=ee.Image(assetPath(c,k));
  });
  return imgs;
}

// Geometry
var GAUL0=ee.FeatureCollection('FAO/GAUL/2015/level0');
function countryGeom(c){
  return GAUL0.filter(ee.Filter.eq('ADM0_NAME',cfg(c).gaul)).geometry();
}

// State
var state={
  country:selectedCountry,
  year:maxYear(selectedCountry),
  images:loadImages(selectedCountry),
  layersPanel:null,
  activeMapLayer:null,
  activeDetailsPanel:null,
  activeExpandButton:null
};

// UI icons
var b64 = {
  plusIcon:  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAACXBIWXMAAAsTAAALEwEAmpwYAAABDUlEQVR4nK2TTWoCQRCFP1yqo7cwuY6JGbLXmAuIaPQ2otkIwSP4E5K4UI8kJW/RDj09ndEHD5o31W+qqqugGHXgUaxREk1gCvwCG2AubqVNFBOFV+AIdJVdFqb1FJMWmY2AZWRpZvwFDEOZmVmFeFRk+pL9YP04BDJriz4kKr/hilP1LA99MQ9vwIcr7PWnsoZ14MdNeR0IjjFEI3VpWQtYcI0nx8Q4E13NYlx8yuv+hom24W4lG/5ufJQE+HaFidaprGEfGGcH+xjIMjTYDeDku5tqjf67eivgOS9gKNNQP93MVsCgKLCj8m2dfMZm9K4yczPzXbLdtHXaaU6NdjbNHiCmCi+qwINo5yDOmLg6bSbiLREAAAAASUVORK5CYII=",
  minusIcon: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAACXBIWXMAAAsTAAALEwEAmpwYAAAA+ElEQVR4nK2TWQrCQBBEH36q0Vuo53HDf7cLSFDjddQfQTyCGy4f6pGkoQIhJCaOFjRManoq3T1TkI0y0FCUcEQVWABX4AAsFUdxgXJyoQc8gb6qi8O4gXK6WWJTYJOzNRPeAv6nykysQH4UJNqJb9g8Ho5D99R+JUouNDNXDIF5lLjrT64oA5fww4T2/I5jOLIasIptNoFRRlhOFGtp/V/Qkxv+1rLh9uOleMA5SgSykytGwCz+sJ+OVVaAV9LZrmz0rfV2QCstwZeol7OyHTDJSmyrfbNTkrAJjdVmamVJh8ybZqeT3qmFrY2zC3B+FUWgrrD1R7wBe+4zbes/jqAAAAAElFTkSuQmCC"
};

// ========================= LAYER CONFIGURATION =========================
function makeAssetsConfigFire(country,year,images){
  var ann='burned_area_'+year;
  var cov='burned_coverage_'+year;
  var mon='burned_monthly_'+year;
  var cfgLayers={};
  function src(k){return 'MapBiomas Fire C1 '+country+' v'+productVersion(country,k);}

  // Colombia: the annual product holds values 1 and 2; 2 is shown in grey
  var annualVis = (country === COUNTRY.COLOMBIA)
    ? {min:0,max:2,palette:['#ffffff','#ff0000','#808080'],bands:ann}
    : {min:0,max:1,palette:['#ffffff','#ff0000'],bands:ann};
  var annualLegend = (country === COUNTRY.COLOMBIA)
    ? {1:'Fuego Landsat',2:'Fuego MODIS'}
    : {1:'Fuego'};

  if(images.annual)cfgLayers.annual_burned={
    name:'Área quemada anual',
    assetName:'annual_burned_'+country,
    isMultiBand:true,
    visParams:annualVis,
    legend:annualLegend,
    theme:'FIRE',
    source:src('annual'),
    pixel:'Áreas quemadas mapeadas en el respectivo año',
    description:'Mapa binario que identifica áreas quemadas',
    eeObject:images.annual
  };

  if(images.annual_cov)cfgLayers.annual_burned_coverage={
    name:'Cobertura quemada anual',
    assetName:'annual_burned_coverage_'+country,
    isMultiBand:true,
    visParams:{min:0,max:82,palette:PAL_CLASS,bands:cov},
    legend:countryLegend(country),
    theme:'FIRE',
    source:src('annual_cov'),
    pixel:'Uso y cobertura de la tierra predominante en las áreas quemadas en el año',
    description:'Clase de uso/cobertura de las áreas quemadas en el año',
    eeObject:images.annual_cov
  };

  if(images.monthly)cfgLayers.burned_monthly={
    name:'Área quemada mensual',
    assetName:'burned_monthly_'+country,
    isMultiBand:true,
    visParams:{min:1,max:12,palette:PAL_MENSAL,bands:mon},
    legend:{
      1:'Ene',2:'Feb',3:'Mar',4:'Abr',5:'May',6:'Jun',
      7:'Jul',8:'Ago',9:'Sep',10:'Oct',11:'Nov',12:'Dic'
    },
    theme:'FIRE',
    source:src('monthly'),
    pixel:'Mes en que ocurrió la quema.',
    description:'Distribución mensual de las áreas quemadas en el año.',
    eeObject:images.monthly
  };

  var labelsPer={
    1:'< 5 ha',2:'5 - 25 ha',3:'25 - 50 ha',
    4:'50 - 250 ha',5:'250 - 500 ha',6:'500 - 1,000 ha',
    7:'1,000 - 5,000 ha',8:'>= 5,000 ha'
  };
  
  var labelsDefault={
    1:'< 10 ha',2:'10 - 250 ha',3:'250 - 500 ha',
    4:'500 - 5,000 ha',5:'5,000 - 10,000 ha',6:'10,000 - 50,000 ha',
    7:'50,000 - 100,000 ha',8:'>= 100,000 ha'
  };

  if(images.scar_size)cfgLayers.scar_size_range={
    name:'Tamaño de Cicatriz',
    assetName:'scar_size_range_'+country,
    isMultiBand:true,
    visParams:{
      min:1,max:8,palette:PAL_TAMANHO,
      bands:'scar_area_ha_'+year
    },
    legend:COUNTRY.PERU || country === COUNTRY.CHILE?labelsPer:labelsDefault,
    theme:'FIRE',
    source:src('scar_size'),
    pixel:'Intervalo de tamaño de las cicatrices de quema.',
    description:'Clasifica las cicatrices de fuego en rangos de tamaño.',
    eeObject:images.scar_size
  };

  if(images.acc)cfgLayers.accumulated_burned={
    name:'Área quemada acumulada',
    assetName:'accumulated_burned_'+country,
    isMultiBand:true,
    visParams:{
      min:0,max:1,palette:['#ffffff','#ff0000'],
      bands:'fire_accumulated_'+minYear(country)+'_'+maxYear(country)
    },
    legend:{1:'Fuego acumulado (≥1 ocurrencia)'},
    theme:'FIRE',
    source:src('acc'),
    pixel:'Áreas que se quemaron al menos una vez en el período',
    description:'Acumulación de '+minYear(country)+' a '+maxYear(country)+'.',
    eeObject:images.acc
  };

  if(images.acc_cov)cfgLayers.accumulated_burned_coverage={
    name:'Cobertura quemada acumulada',
    assetName:'accumulated_burned_coverage_'+country,
    isMultiBand:true,
    visParams:{
      min:0,max:82,palette:PAL_CLASS,
      bands:'fire_accumulated_'+minYear(country)+'_'+maxYear(country)
    },
    legend:countryLegend(country),
    theme:'FIRE',
    source:src('acc_cov'),
    pixel:'Uso/cobertura en el último año del intervalo en áreas con fuego acumulado.',
    description:'Clase de uso/cobertura predominante en las áreas quemadas en el período.',
    eeObject:images.acc_cov
  };

  if(images.freq)cfgLayers.fire_frequency={
    name:'Frecuencia de fuego',
    assetName:'fire_frequency_'+country,
    isMultiBand:true,
    visParams:{
      min:0,
      max:totalYears(country),
      palette:frequencyPalette(country),
      bands:'fire_frequency_'+minYear(country)+'_'+maxYear(country)
    },
    legend:{},
    theme:'FIRE',
    source:src('freq'),
    pixel:'Número de veces que el píxel se quemó en el período.',
    description:'Conteo de ocurrencias de fuego entre '+
      minYear(country)+' y '+maxYear(country)+'.',
    eeObject:images.freq
  };

  if(images.last_fire)cfgLayers.year_last_fire={
    name:'Año de la última ocurrencia de fuego',
    assetName:'year_last_fire_'+country,
    isMultiBand:true,
    visParams:{
      min:minYear(country),
      max:maxYear(country),
      palette:lastFirePalette(country),
      bands:'classification_'+maxYear(country)
    },
    legend:{},
    theme:'FIRE',
    source:src('last_fire'),
    pixel:'Año de la última quema registrada',
    description:'Año de la última ocurrencia de quema en el período.',
    eeObject:images.last_fire
  };

  return cfgLayers;
}

// ========================= EXPORT =========================
function exportAll(layersConfig,country,regionGeom){
  var outputFolder='EXPORTS_FIRE_'+country;

  Object.keys(layersConfig).forEach(function(key){
    var layer=layersConfig[key];

    Export.image.toDrive({
      image:layer.eeObject.select(layer.visParams.bands),
      description:layer.assetName,
      folder:outputFolder,
      fileNamePrefix:layer.assetName,
      scale:30,
      region:regionGeom,
      maxPixels:1e13,
      fileFormat:'GeoTIFF'
    });
  });
}
// ========================= UI =========================
// Legend and controls container
var legendContainer = ui.Panel({
  style: { position: 'bottom-left', backgroundColor: '#ffffffaa', border: '1px solid black', margin: '0', borderRadius: '4px', width: '350px', maxHeight: '75%' }
});
var header = ui.Label('LEYENDA', { fontSize: '16px', fontWeight: 'bold', margin: '4px 8px' });
legendContainer.add(header);

// Compact country selector. 
var countrySelect = ui.Select({ 
  items: [ 
    {label:'Argentina', value:COUNTRY.ARGENTINA}, 
    {label:'Bolivia', value:COUNTRY.BOLIVIA}, 
    {label:'Chile', value:COUNTRY.CHILE}, 
    {label:'Colombia', value:COUNTRY.COLOMBIA}, 
    {label:'Paraguay', value:COUNTRY.PARAGUAY}, 
    {label:'Peru', value:COUNTRY.PERU} 
    ], 
  value:selectedCountry, 
  onChange:function(country){switchCountry(country);}, 
  style:{width:'115px', margin:'0'} }); 
var controlsPanel = ui.Panel({ 
  widgets:[ui.Label('País:',{margin:'5px 5px 0 0'}), countrySelect], 
  layout:ui.Panel.Layout.flow('horizontal'), 
  style:{margin:'4px 8px'} 
}); 
legendContainer.add(controlsPanel);

// Year slider, dynamic by country
var yearSlider=ui.Slider({
  min:minYear(state.country),
  max:maxYear(state.country),
  value:maxYear(state.country),
  step:1,
  onChange:function(v){
    state.year=v;
    rebuildLayers();
  },
  style:{stretch:'horizontal'}
});
var yearRow = ui.Panel([ui.Label('Año:'), yearSlider], ui.Panel.Layout.flow('horizontal'), { margin: '0 8px 8px 8px' });
legendContainer.add(yearRow);

// Layers panel
state.layersPanel = ui.Panel({ style: { margin: '0 8px 8px 8px' } });
legendContainer.add(state.layersPanel);

Map.add(legendContainer);

// Optional export button
var exportBtn = ui.Button({
  label:'Exportar todas las capas disponibles',
  onClick: function(){
    var region = countryGeom(state.country);
    var cfg = makeAssetsConfigFire(state.country, state.year, state.images);
    exportAll(cfg, state.country, region);
    print('Tareas de exportación creadas. Ejecútelas desde la pestaña Tasks.');
  },
  style: { margin: '0 8px 8px 8px' }
});
legendContainer.add(exportBtn);

// ========================= UI HELPER FUNCTIONS =========================
function switchCountry(country){
  state.country=country;
  state.year=maxYear(country);
  if(countrySelect.getValue()!==country){
    countrySelect.setValue(country,false);
  }

  yearSlider.setMin(minYear(country));
  yearSlider.setMax(maxYear(country));
  yearSlider.setValue(state.year,false);

  // Reload the images and the interface
  state.images = loadImages(country);
  Map.centerObject(countryGeom(country), 5);
  rebuildLayers();
}

// Return the color for a categorical value
function getPaletteColor(vis, value) {
  var palette = vis.palette || [];
  var min = (vis.min !== undefined) ? vis.min : 0;
  var max = (vis.max !== undefined) ? vis.max : palette.length - 1;

  if (!palette.length) {
    return '#CCCCCC';
  }

  if (max === min) {
    return palette[0];
  }

  // Compute the right position in the palette
  var index = Math.round(
    ((value - min) / (max - min)) * (palette.length - 1)
  );

  index = Math.max(0, Math.min(index, palette.length - 1));

  var color = palette[index];

  // Ensure the leading #
  if (color && color.charAt(0) !== '#') {
    color = '#' + color;
  }

  return color || '#CCCCCC';
}


// Build a categorical legend
function getCategoricalLegend(legend, vis) {

  var panel = ui.Panel({
    style: {
      margin: '2px 0px 4px 22px',
      padding: '0px',
      backgroundColor: 'ffffff00'
    }
  });

  var keys = Object.keys(legend)
    .map(function(k) {
      return parseInt(k, 10);
    })
    .filter(function(k) {
      return !isNaN(k);
    })
    .sort(function(a, b) {
      return a - b;
    });

  keys.forEach(function(code) {

    var label = legend[code];
    var color = getPaletteColor(vis, code);

    // Color square
    var colorBox = ui.Label('', {
      backgroundColor: color,
      padding: '5px',
      margin: '2px 6px 2px 0px',
    });

    // Class name
    var text = ui.Label(label, {
      fontSize: '10px',
      color: '#333333',
      margin: '1px 0px',
      padding: '1px 0px',
      backgroundColor: 'ffffff00'
    });

    var row = ui.Panel({
      widgets: [
        colorBox,
        text
      ],
      layout: ui.Panel.Layout.flow('horizontal'),
      style: {
        margin: '0px',
        padding: '0px',
        backgroundColor: 'ffffff00'
      }
    });

    panel.add(row);
  });

  return panel;
}

function rebuildLayers() {
  // Clear the panel and the active layer
  state.layersPanel.clear();
  if (state.activeMapLayer) Map.layers().remove(state.activeMapLayer);
  state.activeMapLayer = null; state.activeDetailsPanel = null; state.activeExpandButton = null;
  var cfg = makeAssetsConfigFire(state.country, state.year, state.images);

  // Build the collapsible panels
  Object.keys(cfg).forEach(function(assetKey) {
    var assetConfig = cfg[assetKey];
    var vis = assetConfig.visParams;

    var uiLayer = ui.Map.Layer(assetConfig.eeObject, vis, assetConfig.name);
    var detailsPanel = ui.Panel({ style: { padding: '0', margin: '0px', shown: false, backgroundColor: 'ffffff00' } });
    var expandButton = ui.Button({ imageUrl: b64.plusIcon, style: { margin: '0', padding: '0', backgroundColor: 'ffffff00' } });

    expandButton.onClick(function() {
      var isShown = detailsPanel.style().get('shown');

      if (state.activeMapLayer) { Map.layers().remove(state.activeMapLayer); state.activeMapLayer = null; }
      if (state.activeDetailsPanel && state.activeDetailsPanel !== detailsPanel) {
        state.activeDetailsPanel.style().set('shown', false);
        if (state.activeExpandButton) state.activeExpandButton.setImageUrl(b64.plusIcon);
      }

      if (!isShown) {
        detailsPanel.style().set('shown', true);
        expandButton.setImageUrl(b64.minusIcon);
        Map.add(uiLayer);
        state.activeMapLayer = uiLayer;
        state.activeDetailsPanel = detailsPanel;
        state.activeExpandButton = expandButton;
      } else {
        detailsPanel.style().set('shown', false);
        expandButton.setImageUrl(b64.plusIcon);
        state.activeDetailsPanel = null;
        state.activeExpandButton = null;
      }
    });

    var titleLine = ui.Panel({
      widgets: [
        expandButton,
        ui.Label(assetConfig.name, { fontSize: '12px', margin: '0px 5px 0 0px', fontWeight: 'bold', backgroundColor: 'ffffff00' })
      ],
      layout: ui.Panel.Layout.flow('horizontal'),
      style: { margin: '0px', padding: '0px', backgroundColor: 'ffffff00' }
    });

    var headerPanel = ui.Panel({
      widgets: [titleLine],
      layout: ui.Panel.Layout.flow('vertical'),
      style: { border: '1px solid gray', padding: '4px', margin: '4px 0px 0px 0px', backgroundColor: 'ffffff00' }
    });

    if (assetConfig.description) {
      detailsPanel.add(ui.Label(assetConfig.description, { fontSize: '10px', color: '#444444', margin: '0px', backgroundColor: 'ffffff00' }));
    }

    if (assetConfig.legend &&  Object.keys(assetConfig.legend).length > 0 ) {
      detailsPanel.add(getCategoricalLegend(assetConfig.legend, vis ));
    } else {
      if (assetKey === 'fire_frequency' ||assetKey === 'year_last_fire') { detailsPanel.add(getColorBar(vis));}
    }

    // Band selector to explore other years
    addBandSelectToPanel(detailsPanel, assetConfig, uiLayer);
    state.layersPanel.add(headerPanel);
    state.layersPanel.add(detailsPanel);
  });
}

function addBandSelectToPanel(panel, assetConfig, uiLayer) {
  assetConfig.eeObject.bandNames().evaluate(function(names) {
    if (!names || names.length === 0) return;

    var desired = assetConfig.visParams.bands;
    var initial = (names.indexOf(desired) >= 0) ? desired : names[names.length - 1];

    if (initial !== desired) {
      uiLayer.setVisParams({
        min: assetConfig.visParams.min,
        max: assetConfig.visParams.max,
        palette: assetConfig.visParams.palette,
        bands: initial
      });
    }

    var select = ui.Select({
      items: names,
      value: initial,
      style: { stretch: 'horizontal', margin: '0px', backgroundColor: 'ffffff00' },
      onChange: function(bandName) {
        var newVisParams = {
          min: assetConfig.visParams.min, 
          max: assetConfig.visParams.max,
          palette: assetConfig.visParams.palette, 
          bands: bandName
        };
        uiLayer.setVisParams(newVisParams);
      }
    });
    panel.add(select);
  });
}

function getColorBar(visParams) {
  var min = visParams.min || 0;
  var max = visParams.max || 1;
  var palette = visParams.palette || ['000000', 'ffffff'];
  var style = {
    thumb: { margin: '2px', stretch: 'horizontal', height: '10px', backgroundColor: '00000000' },
    boxLabels: { stretch: 'horizontal', margin: '0px', backgroundColor: 'ffffff00' },
    labelLeft: { fontSize: '10px', textAlign: 'left', stretch: 'horizontal', margin: '0px 0px 0px 4px', backgroundColor: 'ffffff00' },
    labelCenter: { fontSize: '10px', textAlign: 'center', stretch: 'horizontal', margin: '0px', backgroundColor: 'ffffff00' },
    labelRight: { fontSize: '10px', textAlign: 'right', stretch: 'horizontal', margin: '0px 4px 0px 0px', backgroundColor: 'ffffff00' }
  };
  function colorBar(pal) {
    var params = { bbox: [0, 0, 1, 0.1], dimensions: '120x10', format: 'png', min: 0, max: 1, palette: pal };
    return ui.Thumbnail({ image: ee.Image.pixelLonLat().select(0), params: params, style: style.thumb });
  }
  var bar = colorBar(palette);
  var labels = ui.Panel({
    layout: ui.Panel.Layout.flow('horizontal'),
    widgets: [ ui.Label(min.toFixed(0), style.labelLeft), ui.Label(((min+max)/2).toFixed(0), style.labelCenter), ui.Label(max.toFixed(0), style.labelRight) ],
    style: style.boxLabels
  });
  return ui.Panel({ widgets: [bar, labels], layout: ui.Panel.Layout.flow('vertical'), style: { backgroundColor: 'ffffff00', padding: '4px', stretch: 'horizontal', minWidth: '120px' } });
}

// ========================= INITIALIZATION =========================
switchCountry(selectedCountry);
