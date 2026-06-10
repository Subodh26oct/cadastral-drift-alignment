import geopandas as gpd
import rasterio
import matplotlib.pyplot as plt
from shapely.geometry import Polygon
import numpy as np
from pathlib import Path
from bhume import load, patch_for_plot, score
from bhume.geo import geom_to_imagery_crs, open_imagery
from rasterio.windows import from_bounds

def patch_for_plot_single_band(src, geom_4326, pad_m=25.0):
    g = geom_to_imagery_crs(src, geom_4326)
    minx, miny, maxx, maxy = g.bounds
    left, bottom, right, top = minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m

    dl, db, dr, dt = src.bounds
    left, bottom, right, top = max(left, dl), max(bottom, db), min(right, dr), min(top, dt)
    if right <= left or top <= bottom:
        raise ValueError('plot bounding box does not overlap the imagery extent')

    window = from_bounds(left, bottom, right, top, transform=src.transform)
    # Read only band 1 because it is single-band
    gray = src.read(1, window=window)  # (H, W)
    return gray, (left, bottom, right, top)

def main():
    village_dir = 'data/34855_vadnerbhairav_chandavad_nashik'
    village = load(village_dir)
    
    # Load predictions
    preds = gpd.read_file(Path(village_dir) / 'predictions.geojson')
    preds['plot_number'] = preds['plot_number'].astype(str)
    preds = preds.set_index('plot_number', drop=False)
    
    # Find some corrected plots
    corrected_plots = preds[preds['status'] == 'corrected']
    print(f"Total corrected plots: {len(corrected_plots)}")
    
    # Let's find plots that are also in example_truths to show before/after against truth!
    if village.example_truths is not None:
        truth_plots = list(village.example_truths.index)
        print(f"Truth plots: {truth_plots}")
        candidate_pns = [pn for pn in truth_plots if pn in corrected_plots.index]
    else:
        candidate_pns = []
        
    if not candidate_pns:
        candidate_pns = list(corrected_plots.index[:3])
        
    print(f"Selected plots for visualization: {candidate_pns}")
    
    # We will plot for each candidate plot
    for idx, pn in enumerate(candidate_pns[:2]):
        fig, axes = plt.subplots(1, 2, figsize=(12, 6))
        
        # 1. Overlay on Satellite Imagery
        with open_imagery(Path(village_dir) / 'imagery.tif') as img_src:
            o_geom = village.plot(pn) # official
            p_geom = preds.loc[pn, 'geometry'] # predicted
            
            # Extract patch around the plot
            patch = patch_for_plot(img_src, o_geom, pad_m=40.0)
            
            # Plot in map coordinates (img_crs) using the patch bounds
            axes[0].imshow(patch.image, extent=[patch.bounds[0], patch.bounds[2], patch.bounds[1], patch.bounds[3]])
            
            # Reproject geometries to imagery CRS
            o_geom_img = geom_to_imagery_crs(img_src, o_geom)
            p_geom_img = geom_to_imagery_crs(img_src, p_geom)
            
            # Plot outlines
            if o_geom_img.geom_type == 'Polygon':
                x, y = o_geom_img.exterior.xy
                axes[0].plot(x, y, color='red', linewidth=2, label='Official (Start)', linestyle='--')
            else:
                for poly in o_geom_img.geoms:
                    x, y = poly.exterior.xy
                    axes[0].plot(x, y, color='red', linewidth=2, label='Official (Start)', linestyle='--')
                    
            if p_geom_img.geom_type == 'Polygon':
                x, y = p_geom_img.exterior.xy
                axes[0].plot(x, y, color='cyan', linewidth=2.5, label='Predicted (Shifted)')
            else:
                for poly in p_geom_img.geoms:
                    x, y = poly.exterior.xy
                    axes[0].plot(x, y, color='cyan', linewidth=2.5, label='Predicted (Shifted)')
            
            # If truth is available, plot truth
            if village.example_truths is not None and pn in village.example_truths.index:
                t_geom = village.example_truths.loc[pn, 'geometry']
                t_geom_img = geom_to_imagery_crs(img_src, t_geom)
                if t_geom_img.geom_type == 'Polygon':
                    x, y = t_geom_img.exterior.xy
                    axes[0].plot(x, y, color='yellow', linewidth=2, label='Ground Truth', linestyle=':')
                else:
                    for poly in t_geom_img.geoms:
                        x, y = poly.exterior.xy
                        axes[0].plot(x, y, color='yellow', linewidth=2, label='Ground Truth', linestyle=':')
            
            axes[0].set_title("Satellite Imagery Overlay")
            axes[0].legend(loc='upper right')
            axes[0].axis('off')

        # 2. Overlay on Boundary Distance Transform
        with open_imagery(Path(village_dir) / 'boundaries.tif') as bound_src:
            gray_img, bounds_b = patch_for_plot_single_band(bound_src, o_geom, pad_m=40.0)
            axes[1].imshow(gray_img, cmap='gray', extent=[bounds_b[0], bounds_b[2], bounds_b[1], bounds_b[3]])
            
            # Plot outlines on boundary mask
            if o_geom_img.geom_type == 'Polygon':
                x, y = o_geom_img.exterior.xy
                axes[1].plot(x, y, color='red', linewidth=2, label='Official (Start)', linestyle='--')
            else:
                for poly in o_geom_img.geoms:
                    x, y = poly.exterior.xy
                    axes[1].plot(x, y, color='red', linewidth=2, label='Official (Start)', linestyle='--')
                    
            if p_geom_img.geom_type == 'Polygon':
                x, y = p_geom_img.exterior.xy
                axes[1].plot(x, y, color='lime', linewidth=2.5, label='Predicted (Shifted)')
            else:
                for poly in p_geom_img.geoms:
                    x, y = poly.exterior.xy
                    axes[1].plot(x, y, color='lime', linewidth=2.5, label='Predicted (Shifted)')
                    
            if village.example_truths is not None and pn in village.example_truths.index:
                t_geom = village.example_truths.loc[pn, 'geometry']
                t_geom_img = geom_to_imagery_crs(bound_src, t_geom)
                if t_geom_img.geom_type == 'Polygon':
                    x, y = t_geom_img.exterior.xy
                    axes[1].plot(x, y, color='yellow', linewidth=2, label='Ground Truth', linestyle=':')
                else:
                    for poly in t_geom_img.geoms:
                        x, y = poly.exterior.xy
                        axes[1].plot(x, y, color='yellow', linewidth=2, label='Ground Truth', linestyle=':')
                        
            axes[1].set_title("Boundary Mask Overlay")
            axes[1].legend(loc='upper right')
            axes[1].axis('off')
            
        conf = preds.loc[pn, 'confidence']
        method = preds.loc[pn, 'method_note']
        plt.suptitle(f"Plot {pn} Alignment - Confidence: {conf:.2f}\nMethod: {method}", fontsize=14)
        plt.tight_layout()
        
        out_name = f"alignment_example_{pn}.png"
        plt.savefig(out_name, dpi=150)
        plt.close()
        print(f"Saved visualization to {out_name}")

if __name__ == '__main__':
    main()
