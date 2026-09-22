/**
 * @description
 *      This module stores all mapbiomas palettes
 *  
 */

var palettes = {

  
  // Currently available years: 12, 26, 31

    
                  
    'frecuencia12':['#faf3cd','#f9e04e','#e9bc1c','#cf8c15','#b55d11','#9b2d0a','#7e0004','#5b0508','#3c0708','#270506','#150404','#040101'],
    
    'frecuencia13':['#faf3cd','#f9e45f','#f4cf1e','#dfaa19','#c98215','#b45910','#9b2d0a','#820004','#670306','#470709','#2c0506','#170404','#040101'],
    
    'frecuencia26':['#faf3cd','#fced97','#fae154','#f8d823','#eec41d','#e1ae1a','#d49616','#ca8315',
                    '#bd6c12','#b25810','#a6420d','#9a2b0a','#8f1807','#800004','#6d0306','#5d0407','#4b0709',
                    '#410708','#370608','#2e0506','#240505','#1b0404','#130303','#0b0203','#050102','#040101'],
    
    'frecuencia27':['#faf3cd','#fced97','#fae154','#f8d823','#eec41d','#e1ae1a','#d49616','#ca8315','#bd6c12',
                    '#b25810','#a6420d','#9a2b0a','#8f1807','#820004','#750205','#670306','#5d0507','#520608',
                  '#470709','#3e0708','#350607','#2c0506','#210505','#170404','#0f0303','#080202','#040101'],
                  
    'frecuencia31':['faf3cd','fdf1b0','fbe981','fae258','f9de44','f1ca1e','e9bc1c','e2af1a','daa118','d29316',
                    'cc8715','bd6c12','b0540f','a8450d','9f360b','921e08','810004','6a0306','600407','5a0508',
                    '4b0709','3f0708','390608','330607','2d0506','270506','1c0404','170404','0d0203','080202','050102'],
                  
    'frecuencia40':['faf3cd', 'fdf1b0', 'fbe981', 'fae258', 'f9de44', 'f8d71f', 'f1ca1e', 'e9bc1c', 'e2af1a','daa118',
                  'd29316', 'cc8715', 'c57b14', 'bd6c12', 'b76011', 'b0540f', 'a8450d', '9f360b', '992a0a', '921e08',
                  '8c1307', '810004', '770105', '6a0306', '600407', '5a0508', '4b0709', '450709', '3f0708', '390608',
                  '330607', '2d0506', '270506', '220505', '1c0404', '170404', '120303', '0d0203', '080202', '050102'],                  

    'acumulado':['800000'],
    
    'mensual':["a900ff", "6f02ff", "020aff", "0675ff", "06ffff", "ffee00",
               "ff7700", "ff0800", "c20202", "8b0000", "0aa602", "83f27e"],
    
    'tamanho_n1': ['000000', '#d0ae35', '#d0ae35', '#b96d2b', '#ac4b28', '#ac4b28', '#72251d', '#72251d', '#240d00'],
    
    'tamanho_n2': ['#d0ae35', '#c68e2f', '#b96d2b', '#ac4b28', '#933328', '#72251d', '#4e180f', '#240d00'],
    
    'ultimo_fuego_12': ['#000079', '#003781', '#00557f', '#007d96', '#41ACA6', '#94D2BD',
                        '#BF7C27', '#BF4B08', '#AA2016', '#9B2226', '#8B0E0F', '#750000'],
    
    'ultimo_fuego_13':['#000079','#002d81','#004c7b','#007292','#0a9294','#41ACA6','#94D2BD',
                        '#9FBD98','#BF7C27','#C45905','#AA2016','#901417','#750000'],
    
    'ultimo_fuego_26': ['#000079', '#00227f', '#002d81', '#004080', '#004c7b', '#005e86',
                        '#007292', '#007d96', '#0a9294', '#26A09E', '#5DB9AD', '#94D2BD',
                        '#9FBD98', '#B4924D', '#BF7C27', '#C45905', '#B93C0C', '#B42E0F',
                        '#AA2016', '#A6211A', '#9F2222', '#961B1E', '#901417', '#850708','#800000', '#750000'],
                        
    'ultimo_fuego_27':['#000079','#00227f','#002d81','#004080','#004c7b','#005e86','#007292',
                        '#007d96','#0a9294','#26A09E','#5DB9AD','#94D2BD','#9FBD98','#B4924D',
                        '#BF7C27','#C45905','#B93C0C','#B42E0F','#AA2016','#A6211A','#9F2222',
                        '#961B1E','#901417','#850708','#800000','#7A0000','#750000'],                    
    
    'ultimo_fuego_31': ['#000079', '#00147d', '#002d81', '#003781', '#004080', '#00557f',
                        '#005e86', '#00688c', '#007d96', '#008998', '#0a9294', '#41ACA6',
                        '#5DB9AD', '#78C5B5', '#9FBD98', '#AAA772', '#B4924D', '#CA6702',
                        '#C45905', '#BF4B08', '#B42E0F', '#AE2012', '#AA2016', '#A3211E',
                        '#9F2222', '#9B2226', '#901417', '#8B0E0F', '#850708', '#800000', '#750000'],                           
    'lulc': [
              "#ffffff", //[0] Empty
              "#1f8d49", //[1] Forest
              "#000000", //[2] Empty
              "#1f8d49", //[3] Forest Formation
              "#7dc975", //[4] Savanna Formation
              "#04381d", //[5] Mangrove
              "#007785", //[6] Floodable Forest
              "#000000", //[7] Mountain forest
              "#000000", //[8] Empty
              "#7a5900", //[9] Forest Plantation
              "#d6bc74", //[10] Herbaceous and Shrubby Vegetation
              "#519799", //[11] Wetland
              "#d6bc74", //[12] Grassland
              "#d89f5c", //[13] Other non-forest natural formations
              "#ffefc3", //[14] Farming
              "#edde8e", //[15] Pasture
              "#000000", //[16] Empty
              "#000000", //[17] Empty
              "#e974ed", //[18] Agriculture
              "#C27BA0", //[19] Temporary Crop
              "#db7093", //[20] Sugar cane
              "#ffefc3", //[21] Mosaic of Uses
              "#d4271e", //[22] Non vegetated area
              "#ffa07a", //[23] Beach, Dune and Sand Spot
              "#d4271e", //[24] Urban Area
              "#db4d4f", //[25] Other non Vegetated Areas
              "#2532e4", //[26] Water
              "#ffffff", //[27] Not Observed
              "#000000", //[28] Empty
              "#ffaa5f", //[29] Rocky Outcrop
              "#9c0027", //[30] Mining
              "#091077", //[31] Aquaculture
              "#fc8114", //[32] Hypersaline Tidal Flat
              "#2532e4", //[33] River, Lake and Ocean
              "#93dfe6", //[34] Glacier
              "#9065d0", //[35] Palm Oil
              "#d082de", //[36] Perennial Crop
              "#02106f", //[37] Artificial water body
              "#02106f", //[38] Reservoirs
              "#f5b3c8", //[39] Soybean
              "#c71585", //[40] Rice
              "#f54ca9", //[41] Other Temporary Crops
              "#cca0d4", //[42] Open grassland
              "#dbd26b", //[43] Closed grassland
              "#807a40", //[44] Dispersed grassland
              "#000000", //[45] Dispersed woody vegetation
              "#d68fe2", //[46] Coffee
              "#9932cc", //[47] Citrus
              "#e6ccff", //[48] Other Perennial Crops
              "#02d659", //[49] Wooded Sandbank Vegetation
              "#ad5100", //[50] Herbaceous Sandbank Vegetation
              "#000000", //[51] Empty
              "#000000", //[52] Empty
              "#000000", //[53] Empty
              "#000000", //[54] Empty
              "#000000", //[55] Empty
              "#000000", //[56] Empty
              "#000000", //[57] Empty
              "#000000", //[58] Empty
              "#1F8D49", //[59] Primary Forest
              "#5CB85D", //[60] Secondary Forest
              "#f5d5d5", //[61] Salt Flat
              "#ff69b4", //[62] Cotton
              "#C7E0AB", //[63] Steppe
              "#000000", //[64] Empty
              "#b9158a", //[65] Tea
              "#a89358", //[66] Shrubland
              "#C8FFB4", //[67] Dwarf forest
              "#e97a7a", //[68] Other natural non-vegetated area
              "#000000", //[69] Empty
              "#be9e00", //[70] Fog oasis
              "#000000", //[71] Empty
              "#910046", //[72] Other crops
              "#6fc179", //[73] Peatlands
              "#be83f7", //[74] Banana
              "#c12100", //[75] Photovoltaic Power Plant
              "#000000", //[76] Empty
              "#86b074", //[77] Open shrublands
              "#000000", //[78] Empty
              "#67671c", //[79] Pinus plantation
              "#886827", //[80] Eucaliptus plantation
              "#dfeb62", //[81] Andean herbaceous/shrubby vegetation
              "#6fc179", //[82] Flooded Andean herbaceous/shrubby vegetation
              "#ab8231", //[83] Other types of forest plantation
  ],
};

exports.get = function (palette) {

    return palettes[palette];
};