# ParkGrid
<p align="center">
  <img src="park_grid_logo.png" alt="ParkGrid Logo" width="200"/>
</p>

<p align="center">
  A Streamlit web app that helps you find the nearest parking meters in <strong>Boston, MA</strong> — with Google Maps navigation built in.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Built%20With-Streamlit-FF4B4B?logo=streamlit&logoColor=white" />
  <img src="https://img.shields.io/badge/Data-Boston%20Parking%20Meters-4ade80" />
  <img src="https://img.shields.io/badge/License-MIT-blue" />
  <img src="https://img.shields.io/badge/Python-3.8%2B-yellow?logo=python&logoColor=white" />
</p>

***

## Overview

**ParkGrid** lets you drop in your current GPS coordinates, set a search radius, and instantly surface the 10 closest individual parking meter spots — complete with rates, time limits, free-parking windows, and slot counts. One click launches a multi-stop Google Maps route to navigate all of them in order.

Demo: <a href="https://park-grid.vercel.app/">Park Grid</a>

***

## Features

- 📍 **Location-based search** — enter latitude/longitude and pick a radius (250 m → 5 km)
- 🏎️ **Nearest 10 spots** — ranked by walking/driving distance using the Haversine formula
- 💰 **Rich meter data** — rate per hour, operating hours & days, maximum time, slot count, and free-parking conditions
- 🗺️ **Google Maps integration** — single-spot navigation *and* a one-click multi-stop route for all results
- 🌙 **Dark-mode UI** — polished card-based interface with color-coded tags

***

## Demo

Enter your coordinates (default: Boston City Hall — `42.3601, -71.0589`) and click **Find Parking Spots**.

```
Latitude:  42.360100
Longitude: -71.058900
Radius:    1.0 km
```

Results display up to 9 nearby meters, each showing:

| Field | Example |
|---|---|
| Street name | Tremont St |
| Distance | 340 m |
| Rate | $1.25/hr |
| Hours | 8 AM–8 PM · Mon–Sat |
| Max time | 2hr max |
| Slots | 🅿️ 4 slots |
| Free when | Sundays & holidays |

***

## Project Structure

```
ParkGrid/
├── app.py                        # Main Streamlit application
├── parking_grouped_count.csv     # Pre-processed meter data (grouped + counted)
├── boston_parking_meters.csv     # Raw Boston parking meter dataset
├── data/                         # Additional data assets
├── park_grid_logo.png            # App logo
├── LICENSE                       # MIT License
└── README.md
```

***

## Getting Started

### Prerequisites

- Python 3.8 or higher
- `pip`

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/anshulagarwal003/ParkGrid.git
cd ParkGrid

# 2. (Optional) Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install streamlit pandas numpy
```

### Run the App

```bash
streamlit run app.py
```

The app will open at `http://localhost:8501` in your browser.

***

## Data Source

The parking meter data is sourced from the **City of Boston's open data portal**. The raw dataset (`boston_parking_meters.csv`) is preprocessed into `parking_grouped_count.csv`, which aggregates individual meter heads by street location and adds a `SLOT_COUNT` column for the number of meters per cluster.

Key columns used by the app:

| Column | Description |
|---|---|
| `LATITUDE` / `LONGITUDE` | Meter GPS coordinates |
| `STREET` | Street name |
| `BASE_RATE` | Hourly parking rate (numeric, USD) |
| `PAY_RATE` | Display rate string (e.g., `$1.25/hr`) |
| `PAY_HOURS` | Hours when payment is required |
| `PAY_DAYS` | Days when payment is required |
| `PAY_MAX_MINS` | Maximum paid parking duration (minutes) |
| `PARK_NO_PAY` | Free parking conditions |
| `SLOT_COUNT` | Number of meter heads at this location |

***

## How It Works

1. **Load** — `parking_grouped_count.csv` is cached by Streamlit on first run.
2. **Distance** — The [Haversine formula](https://en.wikipedia.org/wiki/Haversine_formula) computes great-circle distance (km) between your coordinates and every meter location.
3. **Filter & rank** — Meters within the selected radius are sorted by distance; the top 9 are shown.
4. **Navigate** — Each result card links to a Google Maps direction URL. A banner at the top generates a single multi-stop route through all results.

***

## Tech Stack

| Layer | Library / Tool |
|---|---|
| Web framework | [Streamlit](https://streamlit.io) |
| Data processing | [Pandas](https://pandas.pydata.org), [NumPy](https://numpy.org) |
| Maps | Google Maps Directions API (URL-based, no key required) |
| Font | [Inter](https://fonts.google.com/specimen/Inter) via Google Fonts |

***


***

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
