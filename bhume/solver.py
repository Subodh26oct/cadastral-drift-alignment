import numpy as np
import geopandas as gpd
import scipy.ndimage
import rasterio
import statistics
import time
from shapely.affinity import translate
from shapely.geometry import Polygon, MultiPolygon

def get_boundary_points(geom, num_points=60):
    """Sample points along the boundary of a Polygon or MultiPolygon."""
    if geom.is_empty:
        return np.zeros((0, 2))
    
    if isinstance(geom, MultiPolygon):
        boundaries = [p.exterior for p in geom.geoms if not p.exterior.is_empty]
    elif isinstance(geom, Polygon):
        boundaries = [geom.exterior]
    else:
        boundaries = [geom]
        
    lengths = [b.length for b in boundaries]
    total_length = sum(lengths)
    if total_length == 0:
        return np.zeros((0, 2))
        
    points = []
    for b, l in zip(boundaries, lengths):
        n = max(5, int(num_points * (l / total_length)))
        dists = np.linspace(0, l, n)
        for d in dists:
            p = b.interpolate(d)
            points.append((p.x, p.y))
    return np.array(points)

def solve_village(village, alpha=0.04) -> gpd.GeoDataFrame:
    """Run Two-Pass Spatially Guided local alignment on a village.
    
    - Pass 1: Local grid search on all plots to find confident 'anchor' plots.
    - Interpolation: Interpolate local drift vectors using IDW.
    - Pass 2: Regularized local grid search guided by the interpolated drift vectors.
    """
    plots = village.plots
    
    # If no boundaries file, fall back to global shift
    if village.boundaries_path is None or not village.boundaries_path.exists():
        print(f"No boundaries.tif found for {village.slug}, falling back to global shift")
        from bhume.baseline import global_median_shift
        return global_median_shift(village)
        
    # Project plots to EPSG:3857 for coordinate system consistency
    with rasterio.open(village.imagery_path) as img_src:
        img_crs = img_src.crs
    plots_3857 = plots.to_crs(img_crs)
    
    # Load boundaries TIFF and calculate distance transform
    with rasterio.open(village.boundaries_path) as src:
        bounds_img = src.read(1)
        # 0 = background, 255 = edge
        dist_px = scipy.ndimage.distance_transform_edt(bounds_img == 0)
        res_x = abs(src.transform[0])
        dist_m = dist_px * res_x
        transform = src.transform
        
    print(f"[{village.slug}] Processing {len(plots)} plots...")
    print(f"[{village.slug}] Resolution: {res_x:.3f} m/px. Image dimensions: {bounds_img.shape}")
    
    # Define thresholds scaled by pixel resolution
    max_loss_thresh = 1.8 * res_x
    contrast_thresh = 0.6 * res_x
    improvement_thresh = 0.3 * res_x
    already_correct_thresh = 1.5 * res_x
    
    anchors = []
    local_alignments = {}
    
    t0 = time.time()
    
    # PASS 1: Anchor Identification
    for pn in plots_3857.index:
        o_geom = plots_3857.loc[pn, 'geometry']
        pts = get_boundary_points(o_geom, num_points=60)
        if len(pts) == 0:
            continue
            
        # Coarse search
        coarse_range = np.arange(-24, 25, 3)
        losses = {}
        for dx in coarse_range:
            for dy in coarse_range:
                shifted_pts = pts + np.array([dx, dy])
                rows, cols = rasterio.transform.rowcol(transform, shifted_pts[:, 0], shifted_pts[:, 1])
                cols = np.clip(cols, 0, dist_m.shape[1] - 1)
                rows = np.clip(rows, 0, dist_m.shape[0] - 1)
                sampled_dists = dist_m[rows, cols]
                losses[(dx, dy)] = np.mean(sampled_dists)
                
        best_coarse = min(losses, key=losses.get)
        best_coarse_loss = losses[best_coarse]
        start_loss = losses[(0, 0)]
        
        # Fine search
        fine_dx = np.arange(best_coarse[0] - 3, best_coarse[0] + 3.1, 1.0)
        fine_dy = np.arange(best_coarse[1] - 3, best_coarse[1] + 3.1, 1.0)
        
        best_loss = best_coarse_loss
        best_dx, best_dy = best_coarse
        for dx in fine_dx:
            for dy in fine_dy:
                shifted_pts = pts + np.array([dx, dy])
                rows, cols = rasterio.transform.rowcol(transform, shifted_pts[:, 0], shifted_pts[:, 1])
                cols = np.clip(cols, 0, dist_m.shape[1] - 1)
                rows = np.clip(rows, 0, dist_m.shape[0] - 1)
                sampled_dists = dist_m[rows, cols]
                loss = np.mean(sampled_dists)
                if loss < best_loss:
                    best_loss = loss
                    best_dx, best_dy = dx, dy
                    
        loss_vals = list(losses.values())
        median_loss = np.median(loss_vals)
        contrast = median_loss - best_loss
        shift_dist = np.sqrt(best_dx**2 + best_dy**2)
        
        # Determine if this plot is a confident anchor
        is_anchor = (
            best_loss < max_loss_thresh and
            contrast > contrast_thresh and
            shift_dist < 22.0 and
            (
                (start_loss - best_loss) > improvement_thresh or
                (start_loss < already_correct_thresh and shift_dist < 3.0)
            )
        )
        
        local_alignments[pn] = {
            'dx': best_dx,
            'dy': best_dy,
            'best_loss': best_loss,
            'start_loss': start_loss,
            'contrast': contrast
        }
        
        if is_anchor:
            anchors.append({
                'plot_number': pn,
                'x': o_geom.centroid.x,
                'y': o_geom.centroid.y,
                'dx': best_dx,
                'dy': best_dy
            })
            
    t1 = time.time()
    print(f"[{village.slug}] Pass 1 finished in {t1-t0:.1f}s. Found {len(anchors)} anchor plots.")
    
    # If we have no anchors, compute global median from truths or local alignment medians
    if len(anchors) < 2:
        print(f"[{village.slug}] Too few anchors ({len(anchors)}). Using median of all local alignments.")
        all_dxs = [align['dx'] for align in local_alignments.values()]
        all_dys = [align['dy'] for align in local_alignments.values()]
        global_dx = statistics.median(all_dxs) if all_dxs else 0.0
        global_dy = statistics.median(all_dys) if all_dys else 0.0
        # Create dummy anchors at village corners to avoid division by zero
        anchors = [
            {'plot_number': 'dummy1', 'x': plots_3857.geometry.centroid.x.min(), 'y': plots_3857.geometry.centroid.y.min(), 'dx': global_dx, 'dy': global_dy},
            {'plot_number': 'dummy2', 'x': plots_3857.geometry.centroid.x.max(), 'y': plots_3857.geometry.centroid.y.max(), 'dx': global_dx, 'dy': global_dy}
        ]
        
    anchor_xs = np.array([a['x'] for a in anchors])
    anchor_ys = np.array([a['y'] for a in anchors])
    anchor_dxs = np.array([a['dx'] for a in anchors])
    anchor_dys = np.array([a['dy'] for a in anchors])
    
    # PASS 2: Guided Local Alignment with Regularization
    predictions = []
    
    for pn in plots_3857.index:
        o_geom_3857 = plots_3857.loc[pn, 'geometry']
        o_geom_4326 = plots.loc[pn, 'geometry']
        pts = get_boundary_points(o_geom_3857, num_points=60)
        
        if len(pts) == 0:
            # Empty geometry fallback
            predictions.append({
                'plot_number': pn,
                'status': 'flagged',
                'confidence': 0.0,
                'method_note': 'empty geometry',
                'geometry': o_geom_4326
            })
            continue
            
        # 1. Compute reference shift using IDW from anchors
        px, py = o_geom_3857.centroid.x, o_geom_3857.centroid.y
        dists = np.sqrt((anchor_xs - px)**2 + (anchor_ys - py)**2)
        # Smooth with epsilon (100.0 meters squared) to avoid division by zero and local noise
        weights = 1.0 / (dists**2 + 100.0)
        weights /= np.sum(weights)
        
        ref_dx = np.sum(weights * anchor_dxs)
        ref_dy = np.sum(weights * anchor_dys)
        
        # 2. Regularized fine search around reference shift
        # Coarse-fine grid search around reference shift
        dx_range = np.arange(ref_dx - 6.0, ref_dx + 6.1, 1.0)
        dy_range = np.arange(ref_dy - 6.0, ref_dy + 6.1, 1.0)
        
        best_loss = float('inf')
        best_dx, best_dy = ref_dx, ref_dy
        
        for dx in dx_range:
            for dy in dy_range:
                shifted_pts = pts + np.array([dx, dy])
                rows, cols = rasterio.transform.rowcol(transform, shifted_pts[:, 0], shifted_pts[:, 1])
                cols = np.clip(cols, 0, dist_m.shape[1] - 1)
                rows = np.clip(rows, 0, dist_m.shape[0] - 1)
                sampled_dists = dist_m[rows, cols]
                
                # Loss = average distance to edge + L2 penalty from reference shift
                penalty = alpha * ((dx - ref_dx)**2 + (dy - ref_dy)**2)
                loss = np.mean(sampled_dists) + penalty
                
                if loss < best_loss:
                    best_loss = loss
                    best_dx, best_dy = dx, dy
                    
        # Sub-meter fine search +/- 1.5m around best
        fine_dx_range = np.arange(best_dx - 1.5, best_dx + 1.6, 0.5)
        fine_dy_range = np.arange(best_dy - 1.5, best_dy + 1.6, 0.5)
        
        for dx in fine_dx_range:
            for dy in fine_dy_range:
                shifted_pts = pts + np.array([dx, dy])
                rows, cols = rasterio.transform.rowcol(transform, shifted_pts[:, 0], shifted_pts[:, 1])
                cols = np.clip(cols, 0, dist_m.shape[1] - 1)
                rows = np.clip(rows, 0, dist_m.shape[0] - 1)
                sampled_dists = dist_m[rows, cols]
                
                penalty = alpha * ((dx - ref_dx)**2 + (dy - ref_dy)**2)
                loss = np.mean(sampled_dists) + penalty
                
                if loss < best_loss:
                    best_loss = loss
                    best_dx, best_dy = dx, dy
                    
        # 3. Restraint check: if the starting loss is already very low and shift is small, don't move it
        start_loss = local_alignments[pn]['start_loss']
        shift_dist = np.sqrt(best_dx**2 + best_dy**2)
        
        if start_loss < 1.3 * res_x and shift_dist < 4.0:
            best_dx, best_dy = 0.0, 0.0
            best_loss = start_loss
            shift_dist = 0.0
            
        # 4. Confidence Score Calculation
        contrast = local_alignments[pn]['contrast']
        score_loss = np.exp(-best_loss / (3.0 * res_x))
        score_contrast = 1.0 - np.exp(-contrast / (1.5 * res_x))
        score_deviation = np.exp(-np.sqrt((best_dx - ref_dx)**2 + (best_dy - ref_dy)**2) / 12.0)
        
        # Combine metrics into a calibrated confidence score
        confidence = float(score_loss * score_contrast * score_deviation)
        confidence = np.clip(confidence, 0.0, 1.0)
        
        # 5. Output Prediction Construction
        # Flag plots that have extremely poor confidence or very far out shifts
        if confidence < 0.15:
            status = 'flagged'
            pred_geom = o_geom_4326
            method_note = f'flagged: low confidence ({confidence:.2f})'
        else:
            status = 'corrected'
            # Translate geometry in EPSG:3857 and project back to EPSG:4326
            pred_geom_3857 = translate(o_geom_3857, best_dx, best_dy)
            pred_geom = gpd.GeoSeries([pred_geom_3857], crs=img_crs).to_crs('EPSG:4326').iloc[0]
            method_note = f'aligned (dx={best_dx:.1f}m, dy={best_dy:.1f}m, conf={confidence:.2f})'
            
        predictions.append({
            'plot_number': pn,
            'status': status,
            'confidence': confidence if status == 'corrected' else 0.0, # contract says numeric confidence
            'method_note': method_note,
            'geometry': pred_geom
        })
        
    predictions_gdf = gpd.GeoDataFrame(predictions)
    predictions_gdf = predictions_gdf.set_crs('EPSG:4326')
    predictions_gdf = predictions_gdf.set_index('plot_number', drop=False)
    
    t2 = time.time()
    print(f"[{village.slug}] Solver completed in {t2-t1:.1f}s.")
    
    return predictions_gdf
