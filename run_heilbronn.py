"""
Main runner for Heilbronn Metro Map Generation
Interactive route selection and map generation
"""

from gtfs_loader import GTFSLoader
from octilinear_generator import OctilinearMapGenerator
from visualizer import MapVisualizer
import argparse
from pathlib import Path

def print_available_routes(loader: GTFSLoader):
    """Print all available routes for selection"""
    routes = loader.get_available_routes()
    
    print("\n" + "="*80)
    print("AVAILABLE ROUTES IN HEILBRONN GTFS DATA")
    print("="*80)
    print(f"{'#':<4} {'Route ID':<15} {'Short Name':<15} {'Long Name'}")
    print("-"*80)
    
    for i, (route_id, short_name, long_name) in enumerate(routes, 1):
        print(f"{i:<4} {route_id:<15} {short_name:<15} {long_name[:40]}")
    
    print("="*80)
    return routes

def select_routes_interactive(loader: GTFSLoader):
    """Interactive route selection"""
    routes = print_available_routes(loader)
    
    print("\nRoute Selection Options:")
    print("  1. Enter route numbers (e.g., '1,3,5' or '1-5')")
    print("  2. Enter 'all' for all routes")
    print("  3. Enter route IDs directly (e.g., 'route_1,route_2')")
    
    selection = input("\nYour selection: ").strip()
    
    if selection.lower() == 'all':
        print(f"\n✓ Selected all {len(routes)} routes")
        return None  # None means all routes
    
    selected_route_ids = []
    
    # Try to parse as numbers
    try:
        if '-' in selection:
            # Range selection
            start, end = map(int, selection.split('-'))
            selected_route_ids = [routes[i-1][0] for i in range(start, end+1)]
        elif ',' in selection:
            # Multiple selection
            numbers = [int(n.strip()) for n in selection.split(',')]
            selected_route_ids = [routes[n-1][0] for n in numbers]
        else:
            # Single number
            num = int(selection)
            selected_route_ids = [routes[num-1][0]]
    except (ValueError, IndexError):
        # Assume direct route IDs
        selected_route_ids = [rid.strip() for rid in selection.split(',')]
    
    # Show selected routes
    print(f"\n✓ Selected {len(selected_route_ids)} routes:")
    for route_id in selected_route_ids:
        route_info = next((r for r in routes if r[0] == route_id), None)
        if route_info:
            _, short_name, long_name = route_info
            print(f"  • {short_name}: {long_name}")
    
    return selected_route_ids

def main():
    parser = argparse.ArgumentParser(
        description='Generate octilinear metro map from Heilbronn GTFS data'
    )
    parser.add_argument(
        'gtfs_path',
        type=str,
        help='Path to GTFS data directory'
    )
    parser.add_argument(
        '--routes',
        type=str,
        default=None,
        help='Comma-separated route IDs (e.g., "route_1,route_2") or "all"'
    )
    parser.add_argument(
        '--grid-size',
        type=float,
        default=200.0,
        help='Grid cell size in meters (default: 200)'
    )
    
    parser.add_argument(
        '--min-station-distance',
        type=float,
        default=0.0,
        help='Minimum distance between placed stations in meters (0 = disabled)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='heilbronn_metro_map.png',
        help='Output file path (default: heilbronn_metro_map.png)'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Interactive route selection'
    )
    parser.add_argument(
        '--no-save',
        action='store_true',
        help='Do not save to file, just display'
    )
    
    args = parser.parse_args()
    
    # Check GTFS path exists
    gtfs_path = Path(args.gtfs_path)
    if not gtfs_path.exists():
        print(f"Error: GTFS path '{gtfs_path}' does not exist!")
        return 1
    
    print("\n" + "="*80)
    print("HEILBRONN OCTILINEAR METRO MAP GENERATOR")
    print("="*80)
    
    # Load GTFS data
    #print(f"\nGTFS Path: {gtfs_path}")
    loader = GTFSLoader(str(gtfs_path))
    loader.load_all()
    
    # Select routes
    if args.interactive:
        selected_routes = select_routes_interactive(loader)
    elif args.routes:
        if args.routes.lower() == 'all':
            selected_routes = None
        else:
            selected_routes = [r.strip() for r in args.routes.split(',')]
            print(f"\n✓ Selected routes: {selected_routes}")
    else:
        # Default: show options and ask
        selected_routes = select_routes_interactive(loader)
    
    # Build network
    network_data = loader.build_network(selected_routes,top_k_patterns=1)
    
    # Generate octilinear map
    generator = OctilinearMapGenerator(
        network_data,
        grid_size=args.grid_size,
        min_station_distance_m=args.min_station_distance
    )
    map_data = generator.generate()
    
    # Add route info for legend
    map_data['routes'] = network_data['routes']
    
    #enh = EnhancedMapVisualizer(map_data)
    #enh.create_figure(figsize=(12, 12), title="Enhanced Octilinear Map")
    #enh.draw()
    #enh.save("heilbronn_metro_map_enhanced.png")

    # Visualize
    visualizer = MapVisualizer(map_data)
    visualizer.create_figure()
    visualizer.draw()
    
    # Save or show
    if not args.no_save:
        visualizer.save(args.output)
        print(f"\n✓ Map saved to: {args.output}")
    
    print("\nDisplaying interactive plot...")
    print("  Close the window to exit")
    #visualizer.show()
    
    print("\n" + "="*80)
    print("COMPLETED SUCCESSFULLY")
    print("="*80 + "\n")
    
    return 0

    enh = EnhancedMapVisualizer(map_data)
    enh.create_figure(figsize=(12, 12), title="Enhanced Octilinear Map")
    enh.draw()
    enh.save("heilbronn_metro_map_enhanced.png")
    print("✓ Enhanced map saved to: heilbronn_metro_map_enhanced.png")
    enh.show


if __name__ == "__main__":
    exit(main())