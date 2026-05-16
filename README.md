# 🚇 MetroMapsProject

<p align="center">
Generate beautiful octilinear schematic transit maps from real-world GTFS transit networks.
</p>

<p align="center">
Python • Graph Algorithms • A* Search • Transit Data • Data Visualization
</p>

---

## 📖 About the Project

This project implements the approximation algorithm from:

> **Metro Maps on Octilinear Grid Graphs**  
> Hannah Bast, Patrick Brosi, and Sabine Storandt  
> EuroVis 2020

The goal is to transform real-world transit networks into clean **metro-style schematic maps**, where all routes follow:

⬆ Horizontal  
↗ 45° Diagonal  
➡ Vertical  
↘ Octilinear directions only

Instead of geographic accuracy, the focus is readability and aesthetics — similar to subway maps used in cities worldwide.

---

## ✨ Features

✔ GTFS transit feed support  
✔ Automatic station graph construction  
✔ Degree-2 graph contraction heuristic  
✔ Octilinear A* path routing  
✔ Bend-aware path optimization  
✔ Local search improvement phase  
✔ Topology preservation techniques  
✔ Geographic vs schematic side-by-side rendering

---

## 🛠 Pipeline Overview

The application follows this workflow:

```text
GTFS Data
    ↓
Load Transit Network
    ↓
Build Station Graph
    ↓
Contract Degree-2 Nodes
    ↓
Generate Octilinear Grid
    ↓
A* Route Optimization
    ↓
Local Search Improvements
    ↓
Restore Contracted Stations
    ↓
Render Final Metro Map
```

---

## 📂 Project Structure

```text
MetroMapsProject/
├── run_heilbronn.py
│   └── Main CLI entry point

├── octilinear_generator.py
│   └── Core routing algorithm

├── gtfs_loader.py
│   └── GTFS parser & graph builder

├── heilbronn_data_loader.py
│   └── GTFS / OSM loader

├── visualizer.py
│   └── Geographic + schematic renderer

├── enhanced_visualizer.py
│   └── Alternative visual styling

├── verify_gtfs.py
│   └── GTFS validator utility

└── requirements.txt
```

---

## ⚙ Installation

Requires:

- Python 3.8+

Install dependencies:

```bash
pip install -r requirements.txt
```

Required packages:

```text
numpy
pandas
matplotlib
```

---

# 🚀 Usage

## Basic Example

```bash
python run_heilbronn.py /path/to/gtfs/
```

Launches an interactive route selector where you can choose routes from the transit feed.

---

## Example Commands

### Generate all routes

```bash
python run_heilbronn.py ./gtfs_data/ --routes all
```

### Use a finer grid

```bash
python run_heilbronn.py ./gtfs_data/ \
--routes "101,102,103" \
--grid-size 150
```

### Enforce station spacing

```bash
python run_heilbronn.py ./gtfs_data/ \
--routes all \
--min-station-distance 400
```

### Validate GTFS input

```bash
python verify_gtfs.py ./gtfs_data/
```

---

## 📥 Expected GTFS Files

| File | Required |
|-------|-----------|
| stops.txt | ✅ |
| routes.txt | ✅ |
| trips.txt | ✅ |
| stop_times.txt | ✅ |
| shapes.txt | Optional |

---

# 🧠 Algorithm Overview

This implementation follows Section 4 of the original paper.

---

## Degree-2 Contraction

Stations with exactly two neighbors are contracted before routing.

Benefits:

- Faster computation
- Cleaner visual output
- Reduced complexity

Contracted nodes are later reinserted into final paths.

---

## Octilinear Grid Construction

A regular grid is built over the transit network:

- Horizontal edges
- Vertical edges
- 45° diagonals

Stations are assigned candidate positions near their real coordinates.

---

## 🔍 A* Routing with Bend Penalties

Edges are routed using shortest-path A* search.

### Bend Costs

| Turn | Cost |
|---|---|
| Straight | 0 |
| 135° | 1 |
| 90° | 1.5 |
| Sharp 45° | 2 |
| Reverse | 10 |

Diagonal movement incurs small additional penalties.

---

## 🔄 Local Search Optimization

After initial routing:

- Stations move to neighboring positions
- Connected routes are rerouted
- Improved layouts are accepted

Objective function:

```text
Total Cost = Hops + 1.5 × Bends
```

This improves readability while minimizing unnecessary turns.

---

## 🗺 Output

The renderer creates two side-by-side visualizations:

### Left
🌍 Geographic map

- Original coordinates
- Real route geometry

### Right
🚇 Schematic metro map

- Octilinear routing
- Simplified layout
- Grid-based station placement

Each route receives its own color.

---

## ⚠ Known Limitations

### No station labels

Labels are not rendered by default.

### Large networks can be slow

For larger datasets:

- increase `--grid-size`
- limit route selection

### Exact ILP not implemented

Only the approximation algorithm is included.

### No obstacle handling

Features such as rivers and park constraints are not currently exposed.

### Crossings may occur

Topology preservation is approximated and not mathematically guaranteed.

---

## 📚 Reference

```bibtex
@article{bast2020metro,
title={Metro Maps on Octilinear Grid Graphs},
author={Bast, Hannah and Brosi, Patrick and Storandt, Sabine},
journal={Computer Graphics Forum},
volume={39},
number={3},
year={2020}
}
```

---

## 🔗 Original Paper Demo

http://octi.cs.uni-freiburg.de

---
