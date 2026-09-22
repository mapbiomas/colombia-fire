/* MAPBIOMAS FIRE - COLLECTION 1 - REFERENCE SCRIPT (e.g. COLOMBIA)
 *
 * Final export of the fire scar collection with and without the land use and land cover (LULC) mask.
 *
 * 📅 DATE: May 2026
 *
 * TEAM:
 * Fire scar mapping working group - MapBiomas Fire
 * - Instituto de Pesquisa Ambiental da Amazonia (IPAM)
 * - Wallace Silva and Vera Laisa
 *
 * -------------------------------------------------------------
 * 📌 WHAT DOES THIS SCRIPT DO?
 * This script automates copying the final fire scar images to the designated
 * target collections in Google Earth Engine. It performs the following tasks:
 * 1. **Collection check and creation**: makes sure the target ImageCollections
 * (with and without the LULC mask) exist. If they are not found, it creates them automatically.
 * 2. **Iteration over the final images**: goes through a predefined list (`final_collection`)
 * of image IDs representing the final fire scar classifications.
 * 3. **Asset copy**: for each ID in the list, the script looks for the source image
 * and copies it to the corresponding target collection.
 * 4. **Duplicate handling (optional)**: if an image already exists in the target collection,
 * the script can optionally delete the existing version before copying the new one,
 * avoiding duplicates or outdated versions (controlled by `ELIMINA_SI_YA_EXISTE`).
 * 5. **Listing of available IDs (optional)**: allows printing to the console every
 * image ID available in the source path, making it easier to pick and add them
 * to the `final_collection` list.
 *
 * -------------------------------------------------------------
 * 🔧 WHAT SHOULD I MODIFY TO USE THIS SCRIPT?
 * ✅ **Edit the `final_collection` list**: replace the example IDs with the IDs of your final images
 * that you want to copy to the target collections.
 * ✅ **Check/adjust the target paths**: make sure `col_nomask_id` and `col_mask_id`
 * point to the right locations of your target collections.
 * ✅ **(Optional) Enable deletion of existing assets**: if you want the script to delete
 * an asset in the target before copying a new one with the same name, set the variable
 * `ELIMINA_SI_YA_EXISTE` to `true`. It defaults to `false` for safety.
 * ✅ **(Optional) Enable the ID listing**: to see a list of every image ID
 * available in the source path, so you can copy and paste them into `final_collection`,
 * set `MOSTRAR_TODAS_LAS_VERSIONES_POR_REGION` to `true`.
 *
 * -------------------------------------------------------------
 * ⚠️ IMPORTANT RECOMMENDATION:
 * To avoid errors and make the process easier to review,
 * it is recommended to run the script **one region at a time**.
 * To do so, comment out (with `//`) or temporarily remove the other entries
 * in the `final_collection` list.
 * -------------------------------------------------------------
 * 🖱️ NOTE ABOUT THE OPERATIONS:
 * For every image copied or deleted, the browser will show
 * a pop-up window asking for manual confirmation.
 * Make sure to accept or reject it as appropriate for each asset.
 ***************************************************************/

// 🌍 Country under analysis
var country = 'colombia'; 
var coll_n = '1';

// IDs of the target collections
var col_nomask_id = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION'+coll_n+'/CLASSIFICATION_COLLECTIONS/collection'+coll_n+'_fire_no_mask_v1/';
var col_mask_id   = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION'+coll_n+'/CLASSIFICATION_COLLECTIONS/collection'+coll_n+'_fire_mask_v1/';

// 🔄 Function that creates the collection only if it does not exist
function createAssetIfNotExists(assetId) {
  try {
    ee.data.getAsset(assetId);
    print('✅ La colección ya existe:', assetId);
  } catch (e) {
    print('🆕 Creando colección:', assetId);
    ee.data.createAsset({type:'ImageCollection'}, assetId);
  }
}

// Run for both collections
createAssetIfNotExists(col_nomask_id);
createAssetIfNotExists(col_mask_id);

// 📁 Base path of the source images (final classification by region/year)
var path = 'projects/mapbiomas-' + country + '/assets/FIRE/COLLECTION'+coll_n+'/CLASSIFICATION/';

// ✅ Setting: turn this on if you want to delete the existing target asset and replace it with a new copy.
var ELIMINA_SI_YA_EXISTE = false;

// 🛠 Setting: turn this on if you want to print every available image ID to the console
// so you can copy and paste them into the "final_collection" list declared below
var MOSTRAR_TODAS_LAS_VERSIONES_POR_REGION = true;

ee.Number(0).evaluate(function(a){

  if (MOSTRAR_TODAS_LAS_VERSIONES_POR_REGION !== true){return }

  var panels = ui.Panel();
  var regions = {};
  print('Copie y pegue los IDs a continuación en la variable "final_collection":', panels);

  ee.data.listAssets(path).assets.forEach(function(asset_col){
    // This loop iterates over the asset collections inside the base path.
    if (asset_col.type !== 'IMAGE_COLLECTION'){return } // Makes sure only image collections are processed.
    var name_col = asset_col.id.split('/').slice(-1)[0]; // Extracts the collection name.
    
    try {
      ee.data.listAssets(asset_col.id).assets.forEach(function(asset){
        // This inner loop iterates over the individual images inside each collection.
        var name = asset.id.split('/').slice(-1)[0]; // Extracts the image name.
        var region = name.split('_').slice(-2,-1)[0]; // Determines the region from the image name.
  
        if(regions[region] === undefined){ regions[region] = []} // Initializes the array if the region is new.
  
        regions[region].push('"' + name_col + '/' + name + '",'); // Adds the full image ID.
      });
    } catch (e){
      return ;
    }
  });

  // Sorts and prints the image IDs to the console in an organized way.
  Object.keys(regions).sort().forEach(function(region){
    var _years = {};
    var list = regions[region].sort();
    var region_str = list[0].split('_').slice(-2,-1)[0];
    var panel = ui.Panel([ui.Label(''),ui.Label(''),ui.Label('// // --- --- '+region_str,{fontSize:'22px','font-weight': 'bold',color:'red',margin:'0px'})]);
    panels.add(panel);
    list.forEach(function(str){
      var _year = str.split('_').slice(-1)[0].slice(0,-2);
      if(_years[_year] === undefined){
        _years[_year] = [];
      }
      _years[_year].push(str);
    });

    Object.keys(_years).sort().forEach(function(_year){

      var list = _years[_year].sort();
      var panel_year = ui.Panel([ui.Label(''),ui.Label('// // --- '+ _year,{fontSize:'16px','font-weight': 'bold',color:'green',margin:'0px'})]);
      panel.add(panel_year);

      list.sort().forEach(function(str){
        panel_year.add(ui.Label('// ' + str,{margin:'0px'}));
      });
    });
  });
});


// 📌 List of final images to be copied (EDIT HERE)
// This list holds the IDs of the fire scar images that will be
// exported to the target collections (`col_nomask_id` and `col_mask_id`).
// Make sure the IDs here match the final, approved versions
// of your classifications.
var final_collection = [
   // // --- 2023
  "burned_area_colombia_v150/burned_area_colombia_l89_v2_region92_2023",

];


// 🔁 Iterate over the image list to copy each one
final_collection.forEach(function(id) {

  // 🧩 Prepare names and paths for the copy operation.
  // The version is extracted from the original ID.
  var file_name = id.split('/')[1];              //  image name
  var new_id = file_name.replace(/_v\d+/g, '');  // remove version
  // Builds the full path of the source asset.
  var source_asset = path + id;
  // Builds the full path of the target asset in the collection without a mask.
  var destination_asset = col_nomask_id + new_id;

  try {
    // ✅ Step 1: Check whether the source image really exists in Earth Engine.
    // This prevents errors if an ID in 'final_collection' is wrong or does not exist.
    ee.data.getAsset(source_asset);
    print('ℹ️ Procesando imagen:', source_asset);

    try {
      // ⚠️ Step 2: Try to check whether the asset already exists in the target collection.
      ee.data.getAsset(destination_asset);
      print('✅ El asset ya existe en destino:', destination_asset);

      // If it exists and the deletion option is on, delete it.
      if (ELIMINA_SI_YA_EXISTE) {
        print('🗑️ Eliminando asset existente antes de copiar:', destination_asset);
        ee.data.deleteAsset(destination_asset); // Performs the asset deletion.
        // After deleting, copy the new version.
        print('📤 Copiando nuevo asset al destino:', destination_asset);
        ee.data.copyAsset(source_asset, destination_asset);
      } else {
        // If it exists and the deletion option is off, skip the copy.
        print('⏭️ Carga omitida (el asset ya existe y la eliminación automática no está activada):', destination_asset);
      }

    } catch (e2) {
      // 📤 Step 3: If the asset does NOT exist in the target (the e2 catch fires), copy it directly.
      print('📤 Copiando asset nuevo al destino:', destination_asset);
      ee.data.copyAsset(source_asset, destination_asset);
    }

  } catch (e1) {
    // ⚠️ If the source image is not found, a warning is printed.
    print('⚠️ Imagen de origen no encontrada o ruta incorrecta:', source_asset);
  }
  
});



