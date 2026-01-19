"""
GTFS Data Verification Script
Quick check of your Heilbronn GTFS data
"""

import sys
from pathlib import Path
from gtfs_loader import GTFSLoader

def verify_gtfs(gtfs_path: str):
    """Verify GTFS data and show summary"""
    
    print("\n" + "="*70)
    print("GTFS DATA VERIFICATION")
    print("="*70)
    print(f"\nPath: {gtfs_path}\n")
    
    # Check if path exists
    path = Path(gtfs_path)
    if not path.exists():
        print(f"❌ Error: Path '{gtfs_path}' does not exist!")
        return False
    
    # Check for required files
    required_files = ['stops', 'routes', 'trips', 'stop_times']
    missing = []
    
    print("Checking for required files...")
    for file in required_files:
        file_txt = path / f"{file}.txt"
        file_no_ext = path / file
        
        if file_txt.exists() or file_no_ext.exists():
            print(f"  ✓ {file}")
        else:
            print(f"  ❌ {file} - MISSING!")
            missing.append(file)
    
    if missing:
        print(f"\n❌ Missing required files: {', '.join(missing)}")
        return False
    
    # Try to load data
    print("\nLoading GTFS data...")
    try:
        loader = GTFSLoader(gtfs_path)
        loader.load_all()
    except Exception as e:
        print(f"\n❌ Error loading data: {e}")
        return False
    
    # Show summary
    print("\n" + "-"*70)
    print("DATA SUMMARY")
    print("-"*70)
    print(f"Stops:  {len(loader.stops):>6}")
    print(f"Routes: {len(loader.routes):>6}")
    print(f"Trips:  {len(loader.trips):>6}")
    if loader.stop_times_df is not None:
        print(f"Stop Times: {len(loader.stop_times_df):>6}")
    if loader.shapes_df is not None:
        print(f"Shapes: {len(loader.shapes_df):>6}")
    
    # Show sample routes
    print("\n" + "-"*70)
    print("SAMPLE ROUTES (First 10)")
    print("-"*70)
    
    routes = loader.get_available_routes()[:10]
    for i, (route_id, short_name, long_name) in enumerate(routes, 1):
        print(f"{i:2}. {short_name:10} - {long_name[:50]}")
    
    if len(loader.routes) > 10:
        print(f"\n... and {len(loader.routes) - 10} more routes")
    
    # Show sample stops
    print("\n" + "-"*70)
    print("SAMPLE STOPS (First 10)")
    print("-"*70)
    
    for i, (stop_id, stop) in enumerate(list(loader.stops.items())[:10], 1):
        print(f"{i:2}. {stop.stop_name:40} ({stop.stop_lat:.6f}, {stop.stop_lon:.6f})")
    
    if len(loader.stops) > 10:
        print(f"\n... and {len(loader.stops) - 10} more stops")
    
    # Geographic bounds
    lats = [s.stop_lat for s in loader.stops.values()]
    lons = [s.stop_lon for s in loader.stops.values()]
    
    print("\n" + "-"*70)
    print("GEOGRAPHIC BOUNDS")
    print("-"*70)
    print(f"Latitude:  {min(lats):.6f} to {max(lats):.6f}")
    print(f"Longitude: {min(lons):.6f} to {max(lons):.6f}")
    
    # Check network connectivity
    print("\n" + "-"*70)
    print("NETWORK CONNECTIVITY TEST")
    print("-"*70)
    
    try:
        # Try to build network with first route
        first_route = list(loader.routes.keys())[0]
        print(f"Testing with route: {first_route}")
        
        network = loader.build_network([first_route])
        print(f"  ✓ Network built successfully")
        print(f"  - Nodes: {len(network['nodes'])}")
        print(f"  - Edges: {len(network['edges'])}")
    except Exception as e:
        print(f"  ❌ Error building network: {e}")
        return False
    
    print("\n" + "="*70)
    print("✓ VERIFICATION COMPLETE - DATA IS VALID")
    print("="*70)
    print("\nYou can now run:")
    print(f"  python run_heilbronn.py {gtfs_path} --interactive")
    print("="*70 + "\n")
    
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python verify_gtfs.py <path_to_gtfs_directory>")
        print("Example: python verify_gtfs.py gtfs/")
        sys.exit(1)
    
    gtfs_path = sys.argv[1]
    success = verify_gtfs(gtfs_path)
    sys.exit(0 if success else 1)