#!/usr/bin/env python3
"""
3D Serial Plotter — macOS
Reads  x,y,z  coordinates (one per line, comma-separated) from a serial port
and renders them as an interactive, real-time 3-D scatter/line plot.

Dependencies (see requirements.txt):
    pip install pyserial PyQt5 matplotlib numpy
"""

from __future__ import annotations

import sys
from collections import deque
from typing import Optional

import numpy as np

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("pyserial is not installed.  Run:  pip install pyserial")
    sys.exit(1)

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget,
        QVBoxLayout, QHBoxLayout, QGridLayout,
        QComboBox, QPushButton, QLabel, QSpinBox,
        QLineEdit, QTextEdit, QSizePolicy,
        QGroupBox, QStatusBar, QCheckBox, QFrame,
        QSplitter,
    )
    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt5.QtGui import QFont, QTextCursor
except ImportError:
    print("PyQt5 is not installed.  Run:  pip install PyQt5")
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use("Qt5Agg")
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  registers 3-D projection
except ImportError:
    print("matplotlib is not installed.  Run:  pip install matplotlib")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Colour palette  (Catppuccin Mocha)
# ─────────────────────────────────────────────────────────────────────────────

C_BASE    = "#1e1e2e"   # darkest background
C_MANTLE  = "#181825"   # slightly darker (log bg)
C_SURFACE = "#313244"   # widget backgrounds
C_OVERLAY = "#45475a"   # borders / grid
C_MUTED   = "#6c7086"   # dim text / hints
C_TEXT    = "#cdd6f4"   # primary text
C_BLUE    = "#89b4fa"   # accent / focus
C_GREEN   = "#a6e3a1"   # connect / ok
C_TEAL    = "#94e2d5"   # connect hover
C_RED     = "#f38ba8"   # disconnect / newest point
C_PEACH   = "#fab387"   # warning


# ─────────────────────────────────────────────────────────────────────────────
# Serial reader thread
# ─────────────────────────────────────────────────────────────────────────────

class SerialReaderThread(QThread):
    """Background thread that reads lines from a serial port."""

    point_received = pyqtSignal(float, float, float)  # parsed x,y,z
    line_received  = pyqtSignal(str)                  # non-coordinate text
    error_occurred = pyqtSignal(str)

    def __init__(self, port: str, baud: int) -> None:
        super().__init__()
        self.port  = port
        self.baud  = baud
        self._run  = False
        self._conn: Optional[serial.Serial] = None

    # ── public API ───────────────────────────────────────────────────────────

    def send(self, text: str) -> None:
        """Send a text command (appends \\n automatically)."""
        if self._conn and self._conn.is_open:
            try:
                self._conn.write((text + "\n").encode("utf-8"))
            except serial.SerialException:
                pass

    def stop(self) -> None:
        """Signal the thread to finish and wait for it."""
        self._run = False
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self.wait(2000)

    # ── thread body ──────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            self._conn = serial.Serial(self.port, self.baud, timeout=1)
        except serial.SerialException as exc:
            self.error_occurred.emit(str(exc))
            return

        self._run = True
        while self._run:
            try:
                raw = self._conn.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                parts = line.split(",")
                if len(parts) == 3:
                    try:
                        x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
                        self.point_received.emit(x, y, z)
                        continue
                    except ValueError:
                        pass
                self.line_received.emit(line)

            except serial.SerialException as exc:
                self.error_occurred.emit(str(exc))
                break

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# 3-D matplotlib canvas
# ─────────────────────────────────────────────────────────────────────────────

class Plot3DCanvas(FigureCanvas):
    """Matplotlib 3-D axes embedded in a Qt widget."""

    def __init__(self, max_pts: int = 500) -> None:
        self.fig = Figure(facecolor=C_BASE, tight_layout=True)
        super().__init__(self.fig)

        self.ax = self.fig.add_subplot(111, projection="3d")
        self._max   = max_pts
        self._xs: deque = deque(maxlen=max_pts)
        self._ys: deque = deque(maxlen=max_pts)
        self._zs: deque = deque(maxlen=max_pts)
        self._dirty = False

        self._decorate_axes()
        self.setMinimumSize(560, 460)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ── public ───────────────────────────────────────────────────────────────

    def add_point(self, x: float, y: float, z: float) -> None:
        self._xs.append(x)
        self._ys.append(y)
        self._zs.append(z)
        self._dirty = True

    def flush(self) -> None:
        """Redraw (called by a QTimer — not on every incoming point)."""
        if not self._dirty:
            return
        self._dirty = False
        self._redraw()

    def set_max_points(self, n: int) -> None:
        xs = list(self._xs)[-n:]
        ys = list(self._ys)[-n:]
        zs = list(self._zs)[-n:]
        self._max = n
        self._xs  = deque(xs, maxlen=n)
        self._ys  = deque(ys, maxlen=n)
        self._zs  = deque(zs, maxlen=n)
        self._dirty = True

    def clear(self) -> None:
        self._xs.clear()
        self._ys.clear()
        self._zs.clear()
        self.ax.cla()
        self._decorate_axes()
        self.draw_idle()

    # ── private ──────────────────────────────────────────────────────────────

    def _redraw(self) -> None:
        if not self._xs:
            return

        xs = np.asarray(self._xs)
        ys = np.asarray(self._ys)
        zs = np.asarray(self._zs)

        # Preserve camera orientation
        elev, azim = self.ax.elev, self.ax.azim
        self.ax.cla()
        self._decorate_axes()
        self.ax.view_init(elev=elev, azim=azim)

        n = len(xs)

        # Connecting trail
        if n > 1:
            self.ax.plot(
                xs, ys, zs,
                color=C_BLUE, alpha=0.20, linewidth=0.9,
                zorder=1,
            )

        # Scatter — colour ramp: old→dim, new→bright
        colours = np.linspace(0.1, 1.0, n)
        self.ax.scatter(
            xs, ys, zs,
            c=colours, cmap="plasma",
            s=16, alpha=0.85, depthshade=True,
            zorder=2,
        )

        # Highlight the most-recent point
        self.ax.scatter(
            [xs[-1]], [ys[-1]], [zs[-1]],
            color=C_RED, s=60, zorder=5, depthshade=False,
        )

        self.draw_idle()

    def _decorate_axes(self) -> None:
        ax = self.ax
        self.fig.patch.set_facecolor(C_BASE)
        ax.set_facecolor(C_BASE)
        ax.set_xlabel("X", color=C_TEXT, labelpad=8)
        ax.set_ylabel("Y", color=C_TEXT, labelpad=8)
        ax.set_zlabel("Z", color=C_TEXT, labelpad=8)
        ax.set_title("3D Point Stream", color=C_TEXT, fontsize=12, pad=14)
        ax.tick_params(colors=C_TEXT, labelsize=8)
        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            axis.pane.fill = False
            axis.pane.set_edgecolor(C_OVERLAY)
            axis._axinfo["grid"]["color"] = C_SURFACE


# ─────────────────────────────────────────────────────────────────────────────
# Application stylesheet
# ─────────────────────────────────────────────────────────────────────────────

APP_STYLE = f"""
/* ── root ────────────────────────────────────────────── */
QMainWindow, QWidget {{
    background-color: {C_BASE};
    color: {C_TEXT};
    font-size: 13px;
    font-family: -apple-system, "SF Pro Text", "Helvetica Neue", sans-serif;
}}

/* ── groups ──────────────────────────────────────────── */
QGroupBox {{
    font-weight: 600;
    font-size: 12px;
    border: 1px solid {C_OVERLAY};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {C_BLUE};
}}

/* ── buttons ─────────────────────────────────────────── */
QPushButton {{
    background-color: {C_SURFACE};
    color: {C_TEXT};
    border: 1px solid {C_OVERLAY};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 26px;
}}
QPushButton:hover  {{ background-color: {C_OVERLAY}; border-color: {C_BLUE}; }}
QPushButton:pressed {{ background-color: #585b70; }}
QPushButton:disabled {{ color: {C_MUTED}; border-color: {C_SURFACE}; }}

/* connect button — disconnected state */
QPushButton#connectBtn {{
    background-color: {C_GREEN};
    color: {C_BASE};
    font-weight: 700;
    font-size: 13px;
    min-height: 32px;
}}
QPushButton#connectBtn:hover {{ background-color: {C_TEAL}; }}

/* connect button — connected state */
QPushButton#connectBtn[live="1"] {{
    background-color: {C_RED};
    color: {C_BASE};
}}
QPushButton#connectBtn[live="1"]:hover {{
    background-color: #eba0ac;
}}

/* send button */
QPushButton#sendBtn {{
    background-color: {C_BLUE};
    color: {C_BASE};
    font-weight: 600;
}}
QPushButton#sendBtn:hover {{ background-color: #74c7ec; }}

/* ── inputs ──────────────────────────────────────────── */
QComboBox, QSpinBox, QLineEdit {{
    background-color: {C_SURFACE};
    color: {C_TEXT};
    border: 1px solid {C_OVERLAY};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {C_OVERLAY};
}}
QComboBox QAbstractItemView {{
    background-color: {C_SURFACE};
    color: {C_TEXT};
    selection-background-color: {C_OVERLAY};
    border: 1px solid {C_OVERLAY};
}}
QComboBox::drop-down  {{ border: none; width: 22px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {C_BLUE};
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; }}

/* ── console / log ───────────────────────────────────── */
QTextEdit {{
    background-color: {C_MANTLE};
    color: {C_GREEN};
    border: 1px solid {C_SURFACE};
    border-radius: 6px;
    font-family: "Menlo", "Courier New", monospace;
    font-size: 11px;
}}

/* ── scrollbars ──────────────────────────────────────── */
QScrollBar:vertical {{
    background: {C_BASE};
    width: 8px;
    border-radius: 4px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C_OVERLAY};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── checkbox ────────────────────────────────────────── */
QCheckBox {{ spacing: 7px; color: {C_TEXT}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {C_OVERLAY};
    border-radius: 3px;
    background: {C_SURFACE};
}}
QCheckBox::indicator:checked {{
    background: {C_BLUE};
    border-color: {C_BLUE};
}}

/* ── status bar ──────────────────────────────────────── */
QStatusBar {{
    background: {C_MANTLE};
    color: {C_MUTED};
    font-size: 11px;
    padding: 2px 8px;
}}

/* ── misc labels ─────────────────────────────────────── */
QLabel#hint {{
    color: {C_MUTED};
    font-size: 11px;
    padding: 3px 6px;
}}
QLabel#coordLabel {{
    color: {C_BLUE};
    font-family: "Menlo", monospace;
    font-size: 12px;
}}

/* ── separator ───────────────────────────────────────── */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {C_OVERLAY};
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self._reader: Optional[SerialReaderThread] = None
        self._connected  = False
        self._total_pts  = 0
        self._rate_pts   = 0

        self.setWindowTitle("3D Serial Plotter")
        self.setMinimumSize(1140, 740)

        self._build_ui()
        self.setStyleSheet(APP_STYLE)
        self.refresh_ports()

        # Plot flush timer  ~20 fps
        self._plot_timer = QTimer(self)
        self._plot_timer.timeout.connect(self._canvas.flush)
        self._plot_timer.start(50)

        # Stats ticker  1 s
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._tick_stats)
        self._stats_timer.start(1000)

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 8)
        outer.setSpacing(12)

        # ── Left control panel ────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(262)
        col = QVBoxLayout(left)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        # — Serial connection ——————————————————————————————————————————————
        grp_conn = QGroupBox("Serial Connection")
        g = QVBoxLayout(grp_conn)
        g.setSpacing(8)

        # Port row
        port_row = QHBoxLayout()
        port_lbl = QLabel("Port")
        port_lbl.setFixedWidth(36)
        port_row.addWidget(port_lbl)
        self._port_cb = QComboBox()
        self._port_cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        port_row.addWidget(self._port_cb)
        ref_btn = QPushButton("↻")
        ref_btn.setFixedWidth(30)
        ref_btn.setToolTip("Refresh port list")
        ref_btn.clicked.connect(self.refresh_ports)
        port_row.addWidget(ref_btn)
        g.addLayout(port_row)

        # Baud row
        baud_row = QHBoxLayout()
        baud_lbl = QLabel("Baud")
        baud_lbl.setFixedWidth(36)
        baud_row.addWidget(baud_lbl)
        self._baud_cb = QComboBox()
        for b in ("4800","9600","19200","38400","57600","115200","230400","250000","500000","1000000"):
            self._baud_cb.addItem(b)
        self._baud_cb.setCurrentText("115200")
        baud_row.addWidget(self._baud_cb)
        g.addLayout(baud_row)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.HLine); g.addWidget(sep1)

        self._conn_btn = QPushButton("Connect")
        self._conn_btn.setObjectName("connectBtn")
        self._conn_btn.clicked.connect(self._toggle_conn)
        g.addWidget(self._conn_btn)

        col.addWidget(grp_conn)

        # — Display settings ——————————————————————————————————————————————
        grp_disp = QGroupBox("Display")
        gd = QVBoxLayout(grp_disp)
        gd.setSpacing(8)

        pts_row = QHBoxLayout()
        pts_row.addWidget(QLabel("Max points"))
        self._max_spin = QSpinBox()
        self._max_spin.setRange(10, 100_000)
        self._max_spin.setValue(500)
        self._max_spin.setSingleStep(100)
        self._max_spin.setToolTip("Number of points to keep on the plot")
        self._max_spin.valueChanged.connect(
            lambda v: self._canvas.set_max_points(v)
        )
        pts_row.addWidget(self._max_spin)
        gd.addLayout(pts_row)

        self._verbose_cb = QCheckBox("Log incoming data")
        self._verbose_cb.setToolTip(
            "Append every received coordinate to the console log.\n"
            "Disable for high data rates."
        )
        gd.addWidget(self._verbose_cb)

        clr_btn = QPushButton("Clear Plot")
        clr_btn.clicked.connect(self._clear_plot)
        gd.addWidget(clr_btn)

        col.addWidget(grp_disp)

        # — Coordinate readout ————————————————————————————————————————————
        grp_coord = QGroupBox("Last Point")
        gc = QVBoxLayout(grp_coord)
        self._coord_lbl = QLabel("—")
        self._coord_lbl.setObjectName("coordLabel")
        self._coord_lbl.setAlignment(Qt.AlignCenter)
        self._coord_lbl.setWordWrap(True)
        gc.addWidget(self._coord_lbl)
        col.addWidget(grp_coord)

        # — Send command ———————————————————————————————————————————————————
        grp_cmd = QGroupBox("Send Command")
        gx = QVBoxLayout(grp_cmd)
        gx.setSpacing(6)
        self._cmd_in = QLineEdit()
        self._cmd_in.setPlaceholderText("Type command and press Enter…")
        self._cmd_in.returnPressed.connect(self._send_cmd)
        gx.addWidget(self._cmd_in)
        send_btn = QPushButton("Send ↵")
        send_btn.setObjectName("sendBtn")
        send_btn.clicked.connect(self._send_cmd)
        gx.addWidget(send_btn)
        col.addWidget(grp_cmd)

        # — Console log ————————————————————————————————————————————————————
        grp_log = QGroupBox("Console")
        gl = QVBoxLayout(grp_log)
        gl.setSpacing(5)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(130)
        gl.addWidget(self._log)
        clr_log = QPushButton("Clear Console")
        clr_log.clicked.connect(self._log.clear)
        gl.addWidget(clr_log)
        col.addWidget(grp_log)

        col.addStretch()

        # — Statistics ————————————————————————————————————————————————————
        grp_stat = QGroupBox("Statistics")
        gs = QVBoxLayout(grp_stat)
        self._stats_lbl = QLabel("Points received: 0\nData rate:  0 pts/s")
        self._stats_lbl.setFont(QFont("Menlo", 11))
        self._stats_lbl.setAlignment(Qt.AlignLeft)
        gs.addWidget(self._stats_lbl)
        col.addWidget(grp_stat)

        # ── Right panel (3-D canvas + hint) ───────────────────────────────────
        right = QWidget()
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(4)

        self._canvas = Plot3DCanvas(max_pts=self._max_spin.value())
        right_col.addWidget(self._canvas)

        hint = QLabel(
            "  🖱  Left-drag — orbit   ·   Scroll — zoom   ·   Right-drag — pan"
        )
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        right_col.addWidget(hint)

        outer.addWidget(left)
        outer.addWidget(right, 1)

        # ── Status bar ────────────────────────────────────────────────────────
        self._sb = QStatusBar()
        self.setStatusBar(self._sb)
        self._sb.showMessage("Not connected")

    # ─────────────────────────────────────────────────────────────────────────
    # Slots / actions
    # ─────────────────────────────────────────────────────────────────────────

    def refresh_ports(self) -> None:
        prev = self._port_cb.currentText()
        self._port_cb.clear()
        ports = sorted(p.device for p in serial.tools.list_ports.comports())
        if ports:
            self._port_cb.addItems(ports)
            if prev in ports:
                self._port_cb.setCurrentText(prev)
        else:
            self._port_cb.addItem("— no serial ports found —")

    def _toggle_conn(self) -> None:
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        port = self._port_cb.currentText()
        if not port or port.startswith("—"):
            self._log_msg("⚠  No valid port selected.")
            return

        baud = int(self._baud_cb.currentText())

        self._reader = SerialReaderThread(port, baud)
        self._reader.point_received.connect(self._on_point)
        self._reader.line_received.connect(self._on_line)
        self._reader.error_occurred.connect(self._on_err)
        self._reader.start()

        self._connected = True
        self._conn_btn.setText("Disconnect")
        self._conn_btn.setProperty("live", "1")
        self._refresh_btn_style()

        self._sb.showMessage(f"● Connected — {port}  @  {baud} baud")
        self._log_msg(f"✓ Connected to {port} @ {baud} baud")

    def _disconnect(self) -> None:
        if self._reader:
            self._reader.stop()
            self._reader = None

        self._connected = False
        self._conn_btn.setText("Connect")
        self._conn_btn.setProperty("live", "0")
        self._refresh_btn_style()

        self._sb.showMessage("Not connected")
        self._log_msg("✗ Disconnected.")

    def _on_point(self, x: float, y: float, z: float) -> None:
        self._canvas.add_point(x, y, z)
        self._total_pts += 1
        self._rate_pts  += 1
        self._coord_lbl.setText(f"X {x:.4f}\nY {y:.4f}\nZ {z:.4f}")
        if self._verbose_cb.isChecked():
            self._log_msg(f"  {x:>12.4f}  {y:>12.4f}  {z:>12.4f}")

    def _on_line(self, line: str) -> None:
        self._log_msg(f"← {line}")

    def _on_err(self, err: str) -> None:
        self._log_msg(f"✗ Error: {err}")
        self._disconnect()

    def _send_cmd(self) -> None:
        cmd = self._cmd_in.text().strip()
        if not cmd:
            return
        if not self._connected or not self._reader:
            self._log_msg("⚠  Not connected — cannot send.")
            return
        self._reader.send(cmd)
        self._log_msg(f"→ {cmd}")
        self._cmd_in.clear()

    def _clear_plot(self) -> None:
        self._canvas.clear()
        self._total_pts = 0
        self._log_msg("— Plot cleared.")

    def _tick_stats(self) -> None:
        self._stats_lbl.setText(
            f"Points received:  {self._total_pts:,}\n"
            f"Data rate:  {self._rate_pts:,} pts/s"
        )
        self._rate_pts = 0

    def _log_msg(self, msg: str) -> None:
        """Append to console, trimming old lines if needed."""
        self._log.append(msg)
        doc = self._log.document()
        while doc.blockCount() > 600:
            cur = self._log.textCursor()
            cur.movePosition(QTextCursor.Start)
            cur.select(QTextCursor.BlockUnderCursor)
            cur.removeSelectedText()
            cur.deleteChar()

    def _refresh_btn_style(self) -> None:
        btn = self._conn_btn
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def closeEvent(self, event) -> None:
        self._disconnect()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # HiDPI / Retina support
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)

    app = QApplication(sys.argv)
    app.setApplicationName("3D Serial Plotter")
    app.setApplicationDisplayName("3D Serial Plotter")
    app.setStyle("Fusion")   # consistent cross-macOS-version chrome

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
