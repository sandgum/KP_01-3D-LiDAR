# 3D Serial Plotter

A real-time 3D point plotter for macOS that reads `x,y,z` coordinates from a
serial port and renders them in an interactive 3D view.

---

## Requirements

- Python 3.9 or later
- macOS 11 Big Sur or later (works on Intel & Apple Silicon)

## Installation

```bash
pip install -r requirements.txt
```

Or install manually:

```bash
pip install pyserial PyQt5 matplotlib numpy
```

## Running

```bash
python serial_plotter.py
```

---

## Serial data format

Send one coordinate per line to the serial port:

```
1.23,4.56,7.89
-0.5,2.0,3.14
```

Any line that is **not** three comma-separated numbers is treated as plain text
and shown in the console log — useful for debug messages from your device.

---

## Features

| Feature | Details |
|---|---|
| **Port selector** | Dropdown lists all available `/dev/cu.*` ports with a one-click refresh button |
| **Baud rate** | Selectable from 4800 to 1 000 000 |
| **3D view** | Orbit (left-drag), zoom (scroll), pan (right-drag) |
| **Max points** | Spin-box controls how many points are kept on screen (10 – 100 000) |
| **Colour ramp** | Older points appear dim (purple), newest point highlighted in pink/red |
| **Send commands** | Type any text in the "Send Command" box and press Enter or click Send |
| **Console log** | Displays connection events, sent commands, and non-coordinate serial output |
| **Verbose mode** | Checkbox to also log every received coordinate |
| **Statistics** | Live display of total point count and incoming data rate (pts/s) |
| **Last point** | Coordinates of the most-recently received point shown at all times |

---

## Tips

- **High data rates**: uncheck *Log incoming data* to keep the UI smooth.
- **Retina display**: the app enables high-DPI scaling automatically.
- **Apple Silicon**: works natively; no Rosetta needed.
