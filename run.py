#!/usr/bin/env python3
import sys
from pathlib import Path
from bhume import load, score, write_predictions
from bhume.solver import solve_village

VILLAGES = [
    'data/34855_vadnerbhairav_chandavad_nashik',
    'data/12429_malatavadi_chandgad_kolhapur'
]

def main():
    import os
    # Ensure stdout is in UTF-8
    os.environ['PYTHONUTF8'] = '1'
    
    # Process villages
    dirs = sys.argv[1:] if len(sys.argv) > 1 else VILLAGES
    
    for village_dir in dirs:
        v_path = Path(village_dir)
        if not v_path.exists():
            print(f"Skipping {village_dir} (does not exist)")
            continue
            
        print("\n" + "="*60)
        print(f"Processing village: {v_path.name}")
        print("="*60)
        
        # Load data
        village = load(v_path)
        
        # Solve
        preds = solve_village(village, alpha=0.04)
        
        # Write predictions
        pred_file = v_path / 'predictions.geojson'
        out = write_predictions(pred_file, preds)
        print(f"Wrote predictions to {out}")
        
        # Score if truths are available
        if village.example_truths is not None:
            print("\nScore Card against Public Truths:")
            print(score(preds, village))
            
    print("\nProcessing complete.")

if __name__ == '__main__':
    main()
