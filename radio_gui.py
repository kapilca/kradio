#!/usr/bin/env python3
"""toronto-radio-gui — multi-country radio player (Tkinter + ffplay).

Stations live in stations.json next to this file.
Playback: ffplay (headless audio-only) from the ffmpeg package.
"""
import ctypes
import json
import os
import signal
import subprocess
import sys

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    sys.exit(
        "Tkinter is not installed. Run:\n"
        "    sudo apt install python3-tk\n"
        "then try again."
    )

# When frozen: look for stations.json / ffplay.exe next to the .exe (so
# users can edit them); fall back to the bundled copy (PyInstaller's
# sys._MEIPASS, or Nuitka's data dir).
if "__compiled__" in globals():                    # Nuitka (onefile or onedir)
    HERE = os.path.dirname(os.path.abspath(sys.argv[0]))   # real exe folder
    BUNDLED = os.path.dirname(os.path.abspath(__file__))   # bundled data
elif getattr(sys, "frozen", False):                # PyInstaller
    HERE = os.path.dirname(sys.executable)
    BUNDLED = getattr(sys, "_MEIPASS", HERE)
else:
    HERE = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
    BUNDLED = HERE
STATIONS_FILE = os.path.join(HERE, "stations.json")
if not os.path.isfile(STATIONS_FILE):
    STATIONS_FILE = os.path.join(BUNDLED, "stations.json")


def ffplay_cmd():
    """ffplay next to the app (portable install) if present, else bundled
    copy, else whatever is on PATH."""
    exe = "ffplay.exe" if os.name == "nt" else "ffplay"
    for base in (HERE, BUNDLED):
        local = os.path.join(base, exe)
        if os.path.isfile(local):
            return local
    return exe
MIN_VOL, MAX_VOL, DEFAULT_VOL = 10, 100, 80
POLL_MS = 1000  # how often we check the stream is still alive
BAND_ORDER = ["FM", "AM", "WEB"]

# ---------- theme ----------
BG = "#14171c"        # window background
PANEL = "#1d2129"     # panel background
FG = "#e8eaed"        # main text
DIM = "#8b93a1"       # secondary text
ACCENT = "#e5484d"    # live red
ACCENT2 = "#4cc38a"   # green (playing)
SELBG = "#2a303c"


def load_data():
    """Return list of countries from stations.json."""
    with open(STATIONS_FILE) as f:
        data = json.load(f)
    if "countries" in data:                    # v2 multi-country schema
        return data["countries"]
    # v1 single-country schema (Toronto only) — wrap it
    return [{"id": "CA", "flag": "[CA]", "name": "Canada — Toronto/GTA",
             "stations": data["stations"]}]


def grouped_rows(country):
    """Station rows for a country: FM block, AM block, WEB block."""
    stations = country["stations"]

    def fnum(s):
        try:
            return float(s["freq"])
        except (ValueError, KeyError):
            return 9999.0

    rows = []
    for band in BAND_ORDER:
        block = sorted([s for s in stations if s.get("band", "WEB") == band],
                       key=fnum)
        if not block:
            continue
        rows.append({"sep": True, "freq": "", "name": f"— {band} Band —",
                     "genre": "", "band": band, "url": None})
        rows += block
    return rows


def _pdeathsig():
    """Child helper: ask Linux to kill this process if the parent dies.
    Prevents orphaned ffplay audio streams if the app is killed.
    (POSIX only — never called on Windows.)"""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class Player:
    """Audio playback via ffplay; ffmpeg|aplay fallback if ffplay missing."""

    def __init__(self):
        self.proc = None
        self.volume = DEFAULT_VOL
        self.muted = False
        self.current_url = None

    # -- process helpers ------------------------------------------------
    def _kill(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def _spawn(self):
        url, vol = self.current_url, 0 if self.muted else self.volume
        if not url:
            return
        self._kill()
        env = dict(os.environ, SDL_VIDEODRIVER="dummy")
        # platform tweaks: Linux wants PDEATHSIG, Windows wants no console
        # window (preexec_fn is not supported there at all)
        extra = {}
        if os.name == "nt":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            extra["preexec_fn"] = _pdeathsig
        try:
            self.proc = subprocess.Popen(
                [ffplay_cmd(), "-nodisp", "-autoexit", "-loglevel", "quiet",
                 "-volume", str(vol), "-i", url],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=env, **extra)
        except FileNotFoundError:
            if os.name == "nt":
                raise RuntimeError(
                    "ffplay not found. Install FFmpeg (e.g. "
                    "winget install Gyan.FFmpeg) and make sure ffplay.exe "
                    "is on PATH.") from None
            self.proc = subprocess.Popen(
                "ffmpeg -v quiet -i '%s' -af volume=%d -f s16le -ar 48000 -ac 2 "
                "| aplay -q -f S16_LE -r 48000 -c 2" % (url, vol),
                shell=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- public API ------------------------------------------------------
    def play(self, station):
        self.current_url = station["url"]
        self._spawn()

    def stop(self):
        self._kill()
        self.current_url = None

    def apply_volume(self, volume=None, muted=None):
        """Change volume/mute; restarts the stream with the new level."""
        if volume is not None:
            self.volume = max(MIN_VOL, min(MAX_VOL, int(volume)))
        if muted is not None:
            self.muted = muted
        if self.current_url:
            self._spawn()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None


# --------------------------------------------------------------------------
# System tray (Windows notification area) — pure ctypes, no extra packages.
# Closing the window hides it to the tray and playback keeps going; the tray
# icon's menu is how you reopen or really quit. On non-Windows (or if the
# tray icon can't be created) closing the window exits as before.
# --------------------------------------------------------------------------
try:
    import threading
    import ctypes.wintypes as _wt

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _HAS_TRAY = os.name == "nt"
except Exception:                                   # non-Windows / broken ctypes
    _HAS_TRAY = False

if _HAS_TRAY:
    WM_NULL, WM_DESTROY, WM_CLOSE = 0x0000, 0x0002, 0x0010
    WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
    MF_GRAYED, MF_SEPARATOR, TPM_FLAGS = 0x1, 0x800, 0x0182  # RIGHTBUTTON|NONOTIFY|RETURNCMD

    _LRESULT = ctypes.c_ssize_t
    _WPARAM = ctypes.c_size_t
    _LPARAM = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_void_p, ctypes.c_uint,
                                  _WPARAM, _LPARAM)

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                    ("hCursor", ctypes.c_void_p), ("hBackground", ctypes.c_void_p),
                    ("lpszMenuName", ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p)]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("hWnd", ctypes.c_void_p),
                    ("uID", ctypes.c_uint), ("uFlags", ctypes.c_uint),
                    ("uCallbackMessage", ctypes.c_uint),
                    ("hIcon", ctypes.c_void_p),
                    ("szTip", ctypes.c_wchar * 128),
                    ("dwState", ctypes.c_uint), ("dwStateMask", ctypes.c_uint),
                    ("szInfo", ctypes.c_wchar * 256),
                    ("uVersion", ctypes.c_uint),
                    ("szInfoTitle", ctypes.c_wchar * 64),
                    ("dwInfoFlags", ctypes.c_uint),
                    ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", ctypes.c_void_p)]

    _user32.CreateWindowExW.restype = ctypes.c_void_p
    _user32.CreateWindowExW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p,
                                        ctypes.c_wchar_p, ctypes.c_uint,
                                        ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int,
                                        ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       _WPARAM, _LPARAM]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.GetMessageW.argtypes = [ctypes.POINTER(_wt.MSG), ctypes.c_void_p,
                                    ctypes.c_uint, ctypes.c_uint]
    _user32.DispatchMessageW.restype = _LRESULT
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(_wt.MSG)]
    _user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                     _WPARAM, _LPARAM]
    _user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _user32.LoadImageW.restype = ctypes.c_void_p
    _user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                   ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint]
    _user32.LoadIconW.restype = ctypes.c_void_p
    _user32.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _user32.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_size_t, ctypes.c_wchar_p]
    _user32.TrackPopupMenu.restype = ctypes.c_int
    _user32.TrackPopupMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_void_p, ctypes.c_void_p]
    _shell32.Shell_NotifyIconW.restype = ctypes.c_int
    _shell32.Shell_NotifyIconW.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    class TrayIcon:
        """Tray icon on its own thread with its own message window.

        Callbacks (on_open/on_stop/on_exit) run on the tray thread; the app
        marshals them onto the Tk thread with .after().
        """

        _WM_TRAY = 0x8001                      # WM_APP + 1

        def __init__(self, icon_file, tooltip, on_open, on_stop, on_exit,
                     is_playing):
            self._cb_open, self._cb_stop = on_open, on_stop
            self._cb_exit, self._is_playing = on_exit, is_playing
            self._tip = tooltip[:127]
            self._hwnd = None
            self._ok = False                    # icon successfully added
            self._dead = False                  # shutdown requested
            self._ready = threading.Event()

            self._hicon = None
            if icon_file and os.path.isfile(icon_file):
                sx = _user32.GetSystemMetrics(49) or 16   # SM_CXSMICON
                sy = _user32.GetSystemMetrics(50) or 16   # SM_CYSMICON
                self._hicon = _user32.LoadImageW(
                    None, icon_file, 1, sx, sy, 0x10)      # IMAGE_ICON|FROMFILE
            if not self._hicon:
                self._hicon = _user32.LoadIconW(None, 32512)  # IDI_APPLICATION

            self._wndproc_ref = _WNDPROC(self._wndproc)   # keep alive (GC!)
            self._thread = threading.Thread(target=self._run, daemon=True,
                                             name="kradio-tray")
            self._thread.start()
            self._ready.wait(5)                 # icon added / failed by now

        @property
        def alive(self):
            return self._ok and not self._dead

        # -- called from the tray thread ------------------------------------
        def _run(self):
            hinst = ctypes.WinDLL("kernel32", use_last_error=True) \
                .GetModuleHandleW(None)
            cls = "kradioTray%d" % os.getpid()
            wc = _WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinst
            wc.lpszClassName = cls
            if not _user32.RegisterClassW(ctypes.byref(wc)):
                self._ready.set()
                return
            self._hwnd = _user32.CreateWindowExW(
                0, cls, "kradio tray window", 0, 0, 0, 0, 0,
                ctypes.c_void_p(-3), None, hinst, None)   # HWND_MESSAGE parent
            self._ok = bool(_shell32.Shell_NotifyIconW(
                NIM_ADD, ctypes.byref(self._nid())))
            self._ready.set()
            if not self._ok:
                _user32.DestroyWindow(self._hwnd)
                return
            msg = _wt.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

        def _wndproc(self, hwnd, msg, wparam, lparam):
            try:
                if msg == self._WM_TRAY:
                    m = lparam & 0xFFFF
                    if m in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                        self._safe(self._cb_open)
                    elif m in (WM_RBUTTONUP, 0x007B):     # R up / CONTEXTMENU
                        self._menu()
                    return 0
                if msg == WM_CLOSE:
                    _user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    _shell32.Shell_NotifyIconW(NIM_DELETE,
                                               ctypes.byref(self._nid()))
                    _user32.PostQuitMessage(0)
                    self._ok = False
                    return 0
            except Exception:
                pass
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def _menu(self):
            hmenu = _user32.CreatePopupMenu()
            _user32.AppendMenuW(hmenu, 0, 1, "Open kradio")
            _user32.AppendMenuW(hmenu, 0 if self._is_playing() else MF_GRAYED,
                                2, "Stop playback")
            _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(hmenu, 0, 3, "Exit")
            pt = _wt.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            _user32.SetForegroundWindow(self._hwnd)   # so the menu dismisses
            cmd = _user32.TrackPopupMenu(hmenu, TPM_FLAGS, pt.x, pt.y, 0,
                                         self._hwnd, None)
            _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
            _user32.DestroyMenu(hmenu)
            if cmd == 1:
                self._safe(self._cb_open)
            elif cmd == 2:
                self._safe(self._cb_stop)
            elif cmd == 3:
                self._safe(self._cb_exit)

        def _safe(self, cb):
            try:
                cb()
            except Exception:
                pass

        # -- called from the Tk thread --------------------------------------
        def _nid(self):
            n = _NOTIFYICONDATAW()
            n.cbSize = ctypes.sizeof(n)
            n.hWnd = self._hwnd
            n.uID = 1
            n.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            n.uCallbackMessage = self._WM_TRAY
            n.hIcon = self._hicon
            n.szTip = self._tip
            return n

        def set_tooltip(self, text):
            if not self.alive:
                return
            self._tip = text[:127]
            _shell32.Shell_NotifyIconW(NIM_MODIFY,
                                       ctypes.byref(self._nid()))

        def show_balloon(self, title, text):
            if not self.alive:
                return
            n = self._nid()
            n.uFlags = NIF_INFO
            n.szInfoTitle = title[:63]
            n.szInfo = text[:255]
            n.dwInfoFlags = 1                          # NIIF_INFO
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(n))

        def shutdown(self):
            self._dead = True
            if self._hwnd:
                _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            t = self._thread
            if t.is_alive() and t is not threading.current_thread():
                t.join(1.0)


class RadioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Radio — Toronto/GTA · India")
        self.geometry("780x600")
        self.minsize(600, 440)
        self.configure(bg=BG)
        self.player = Player()
        self.current = None          # dict of the playing station
        self._vol_job = None         # debounce id for volume slider
        self.tray = None             # system-tray icon (close-to-tray)
        self._tray_hinted = False    # shown the "still running" balloon?

        try:
            self.countries = load_data()
        except (OSError, ValueError) as e:
            import traceback
            self.countries = []
            self.after(200, lambda: (messagebox.showerror(
                "Radio — startup error",
                "Could not load stations.json:\n%s\n\nMake sure stations.json "
                "sits next to the app." % e), self._on_close()))
            return
        self.country_labels = [f"{c['flag']}  {c['name']}" for c in self.countries]
        self.country = self.countries[0]

        self._build_style()
        self._build_ui()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        signal.signal(signal.SIGTERM, lambda *_: self._quit_app())
        self.after(POLL_MS, self._poll)

        # tray icon: X hides the window but the radio keeps playing
        if _HAS_TRAY:
            icon_file = next((os.path.join(b, "icon.ico")
                              for b in (HERE, BUNDLED)
                              if os.path.isfile(os.path.join(b, "icon.ico"))),
                             None)
            try:
                self.tray = TrayIcon(
                    icon_file,
                    "kradio — idle",
                    on_open=lambda: self.after(0, self._restore_from_tray),
                    on_stop=lambda: self.after(0, self._stop),
                    on_exit=lambda: self.after(0, self._quit_app),
                    is_playing=lambda: self.player.current_url is not None)
            except Exception:
                self.tray = None   # tray unavailable: closing = exiting

    # ---------- UI construction ----------
    def _build_style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
        st.configure("TCombobox", fieldbackground=PANEL, background=SELBG,
                     foreground=FG, arrowcolor=FG, borderwidth=0)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)],
               background=[("readonly", SELBG)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", "#31425f")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        st.configure("Treeview",
                     background=PANEL, foreground=FG, fieldbackground=PANEL,
                     rowheight=26, borderwidth=0, font=("DejaVu Sans", 11))
        st.configure("Treeview.Heading",
                     background=SELBG, foreground=DIM, relief="flat",
                     font=("DejaVu Sans", 10, "bold"))
        st.map("Treeview",
               background=[("selected", "#31425f")],
               foreground=[("selected", "#ffffff")])
        st.map("Treeview.Heading", background=[("active", SELBG)])
        st.configure("Vertical.TScrollbar",
                     background=SELBG, troughcolor=PANEL, borderwidth=0,
                     arrowcolor=DIM)
        st.configure("Horizontal.TScale", background=BG, troughcolor=PANEL)

    def _build_ui(self):
        # ---- header -----------------------------------------------------
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(head, text="📻 Radio", bg=BG, fg=FG,
                 font=("DejaVu Sans", 18, "bold")).pack(side="left")

        # country selector
        self.country_var = tk.StringVar(value=self.country_labels[0])
        combo = ttk.Combobox(head, textvariable=self.country_var,
                             values=self.country_labels, state="readonly",
                             width=24, font=("DejaVu Sans", 11))
        combo.pack(side="left", padx=(12, 0), ipady=3)
        combo.bind("<<ComboboxSelected>>", self._switch_country)

        self.count_lbl = tk.Label(head, text="", bg=BG, fg=DIM,
                                  font=("DejaVu Sans", 10))
        self.count_lbl.pack(side="right", pady=(8, 0))

        # ---- search box --------------------------------------------------
        searchf = tk.Frame(self, bg=BG)
        searchf.pack(fill="x", padx=16)
        tk.Label(searchf, text="🔍", bg=BG, fg=DIM).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._fill_tree())
        ent = tk.Entry(searchf, textvariable=self.search_var,
                       bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                       font=("DejaVu Sans", 11))
        ent.pack(side="left", fill="x", expand=True, ipady=4, padx=(4, 0))

        # ---- station list ------------------------------------------------
        columns = ("freq", "name", "genre", "band")
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="browse")
        for col, text, width, anchor in (
                ("freq", "Freq", 70, "w"),
                ("name", "Station", 250, "w"),
                ("genre", "Genre", 230, "w"),
                ("band", "Band", 60, "w")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.tag_configure("sep", background=SELBG, foreground=DIM)
        self.tree.pack(fill="both", expand=True, padx=16, pady=(8, 0))
        self.tree.bind("<Double-1>", lambda e: self._play_selected())
        self.tree.bind("<Return>", lambda e: self._play_selected())

        # ---- now playing panel -------------------------------------------
        np = tk.Frame(self, bg=PANEL)
        np.pack(fill="x", padx=16, pady=10)
        self.live_lbl = tk.Label(np, text="● OFF", bg=PANEL, fg=DIM,
                                 font=("DejaVu Sans", 10, "bold"))
        self.live_lbl.pack(side="left", padx=(12, 6), pady=10)
        self.np_name = tk.Label(np, text="Nothing playing — pick a station",
                                bg=PANEL, fg=FG, font=("DejaVu Sans", 14, "bold"))
        self.np_name.pack(side="left", pady=(8, 0))
        self.np_sub = tk.Label(np, text="", bg=PANEL, fg=DIM,
                               font=("DejaVu Sans", 10))
        self.np_sub.pack(side="left", padx=10, pady=(10, 0))

        # ---- controls -----------------------------------------------------
        ctl = tk.Frame(self, bg=BG)
        ctl.pack(fill="x", padx=16, pady=(0, 12))

        self.play_btn = tk.Button(ctl, text="▶  Play", command=self._play_selected,
                                  bg=ACCENT2, fg="#0b1216", activebackground="#63d9a4",
                                  activeforeground="#0b1216", relief="flat",
                                  font=("DejaVu Sans", 11, "bold"), padx=16, pady=4,
                                  cursor="hand2")
        self.play_btn.pack(side="left")
        self.stop_btn = tk.Button(ctl, text="⏹  Stop", command=self._stop,
                                  bg=SELBG, fg=FG, activebackground="#39415a",
                                  activeforeground=FG, relief="flat",
                                  font=("DejaVu Sans", 11, "bold"), padx=16, pady=4,
                                  cursor="hand2")
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.mute_btn = tk.Button(ctl, text="🔊", command=self._toggle_mute,
                                  bg=SELBG, fg=FG, activebackground="#39415a",
                                  activeforeground=FG, relief="flat",
                                  font=("DejaVu Sans", 11), padx=10, pady=4,
                                  cursor="hand2")
        self.mute_btn.pack(side="right")

        self.vol_var = tk.IntVar(value=self.player.volume)
        vol = ttk.Scale(ctl, from_=MIN_VOL, to=MAX_VOL, variable=self.vol_var,
                        command=self._on_volume, style="Horizontal.TScale")
        vol.pack(side="right", fill="x", expand=True, padx=(0, 8), ipady=2)

        self.vol_lbl = tk.Label(ctl, text=f"{self.player.volume}%", bg=BG, fg=DIM,
                                font=("DejaVu Sans", 10), width=4)
        self.vol_lbl.pack(side="right")

        # ---- status bar ----------------------------------------------------
        self.status_var = tk.StringVar(value="Ready — pick a country and double-click a station")
        tk.Label(self, textvariable=self.status_var, bg=PANEL, fg=DIM, anchor="w",
                 font=("DejaVu Sans", 9), padx=10).pack(fill="x", side="bottom")

        self._fill_tree()

    # ---------- station list handling ----------
    def _fill_tree(self):
        q = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        self.rows = []  # tree item id -> station dict
        for s in grouped_rows(self.country):
            if s.get("sep"):
                if not q:
                    iid = self.tree.insert("", "end", values=("", s["name"], "", ""),
                                           tags=("sep",))
                    self.rows.append((iid, None))
                continue
            if q and q not in f"{s.get('freq','')} {s['name']} {s['genre']}".lower():
                continue
            freq = s.get("freq") or "—"
            iid = self.tree.insert(
                "", "end", values=(freq, s["name"], s["genre"], s.get("band", "WEB")))
            self.rows.append((iid, s))
        n = len([1 for _, s in self.rows if s])
        bands = [s.get("band", "WEB") for _, s in self.rows if s]
        self.count_lbl.config(text=f"{n} of {len(self.country['stations'])} stations")

    def _selected_station(self):
        sel = self.tree.selection()
        if not sel:
            kids = self.tree.get_children()
            if kids:
                sel = (kids[0],)
            else:
                return None
        for iid, s in self.rows:
            if iid == sel[0] and s is not None:
                return s
        return None

    # ---------- country switching ----------
    def _switch_country(self, event=None):
        idx = self.country_labels.index(self.country_var.get())
        self.country = self.countries[idx]
        self.title(f"{self.country['flag']} Radio — {self.country['name']}")
        self._fill_tree()
        self.status_var.set(
            f"Switched to {self.country['flag']} {self.country['name']} — "
            f"{len(self.country['stations'])} stations")

    # ---------- playback ----------
    def _play_selected(self):
        s = self._selected_station()
        if not s:
            return
        self.current = s
        try:
            self.player.play(s)
        except Exception as e:
            self.current = None
            messagebox.showerror("Playback error", str(e))
            self.status_var.set(f"✗ {e}")
            return
        self._update_np(f"Connecting to {s['name']}…")

    def _stop(self):
        self.player.stop()
        self.current = None
        self.np_name.config(text="Nothing playing — pick a station", fg=FG)
        self.np_sub.config(text="")
        self.live_lbl.config(text="● OFF", fg=DIM)
        self.play_btn.config(text="▶  Play")
        self.status_var.set("Stopped")
        self._update_tray()

    def _update_np(self, status):
        s = self.current
        if s:
            self.np_name.config(text=s["name"])
            freq = s.get("freq") or "web"
            self.np_sub.config(text=f"{freq} {s.get('band','WEB')} · {s['genre']}")
            self.live_lbl.config(text="● LIVE", fg=ACCENT)
        self.status_var.set(status)
        self._update_tray()

    def _poll(self):
        """Watchdog: notice when a stream dies or finishes connecting."""
        if self.current and self.player.running:
            if self.status_var.get().startswith("Connecting"):
                self._update_np(
                    f"Playing {self.current['name']} · volume {self.player.volume}%")
        elif self.current and not self.player.running:
            if self.status_var.get().startswith("Connecting"):
                self._update_np(f"✗ Couldn't connect to {self.current['name']} — try another station")
                self.live_lbl.config(text="● ERROR", fg="#f5a524")
            else:
                self._update_np(f"Stream ended: {self.current['name']}")
                self.live_lbl.config(text="● OFF", fg=DIM)
            self.current = None
            self._update_tray()
        self.after(POLL_MS, self._poll)

    # ---------- volume ----------
    def _on_volume(self, _value):
        self.vol_lbl.config(text=f"{self.vol_var.get()}%")
        if self._vol_job:            # debounce: wait for slider to settle
            self.after_cancel(self._vol_job)
        self._vol_job = self.after(
            350, lambda: self.player.apply_volume(volume=self.vol_var.get()))

    def _toggle_mute(self):
        self.player.apply_volume(muted=not self.player.muted)
        self.mute_btn.config(text="🔇" if self.player.muted else "🔊")
        self.status_var.set("Muted" if self.player.muted
                            else f"Volume {self.player.volume}%")

    # ---------- misc ----------
    def _bind_keys(self):
        self.bind("<space>", lambda e: self._play_selected()
                  if not self.player.running else self._stop())
        self.bind("<m>", lambda e: self._toggle_mute())
        self.bind("<Escape>", lambda e: self._stop())
        self.bind("<n>", lambda e: self._next_country())
        self.bind("<N>", lambda e: self._prev_country())
        self.bind("<Control-q>", lambda e: self._quit_app())
        self.bind("<Control-Q>", lambda e: self._quit_app())

    def _next_country(self):
        i = self.countries.index(self.country)
        self.country_var.set(self.country_labels[(i + 1) % len(self.countries)])
        self._switch_country()

    def _prev_country(self):
        i = self.countries.index(self.country)
        self.country_var.set(self.country_labels[(i - 1) % len(self.countries)])
        self._switch_country()

    # ---------- tray ----------
    def _update_tray(self):
        if self.tray and self.tray.alive:
            name = self.current["name"] if self.current else "idle"
            self.tray.set_tooltip(f"kradio — {name}")

    def _restore_from_tray(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_close(self):
        """X button: hide to the tray and keep playing (Exit quits for real)."""
        if self.tray and self.tray.alive:
            self.withdraw()
            if not self._tray_hinted:
                self._tray_hinted = True
                self.tray.show_balloon(
                    "kradio is still here",
                    "The window closed to the tray but the radio keeps "
                    "playing. Click the 📻 icon to reopen; right-click for "
                    "options.")
        else:
            self._quit_app()

    def _quit_app(self):
        self.player.stop()
        if self.tray:
            self.tray.shutdown()
        self.destroy()


if __name__ == "__main__":
    if not os.path.exists(STATIONS_FILE):
        sys.exit("stations.json not found next to radio_gui.py")
    RadioApp().mainloop()
