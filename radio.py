#!/usr/bin/env python3
"""toronto-radio — a TUI FM/AM radio for Toronto/GTA internet streams.

Keys:  Up/Down or j/k  select      Enter  play
       Space          stop         r      reconnect
       +/-            volume       m      mute
       q or Q         quit
"""
import curses
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
STATIONS_FILE = os.path.join(HERE, "stations.json")
MAX_VOL, MIN_VOL = 100, 10


def load_data():
    with open(STATIONS_FILE) as f:
        data = json.load(f)
    if "countries" in data:
        return data["countries"]
    return [{"id": "CA", "flag": "\U0001f1e8\U0001f1e6", "name": "Canada \u2014 Toronto/GTA",
             "stations": data["stations"]}]


def grouped(country):
    stations = country["stations"]

    def key(s):
        try:
            return float(s["freq"])
        except (ValueError, KeyError):
            return 9999.0

    rows = []
    for band in ("FM", "AM", "WEB"):
        block = sorted([s for s in stations if s.get("band", "WEB") == band], key=key)
        if block:
            rows.append({"freq": "", "name": f"-- {band} --", "genre": "", "url": None, "sep": True})
            rows += block
    return rows


class Player:
    """Plays a stream via ffplay (audio only); falls back to ffmpeg|aplay."""

    def __init__(self):
        self.proc = None
        self.pipeline = False
        self.volume = 80
        self.muted = False

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

    def stop(self):
        self._kill()

    def play(self, url):
        self._kill()
        vol = 0 if self.muted else self.volume
        env = dict(os.environ, SDL_VIDEODRIVER="dummy")
        self.pipeline = False
        try:
            self.proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                 "-volume", str(vol), "-i", url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except FileNotFoundError:
            self.pipeline = True
            self.proc = subprocess.Popen(
                "ffmpeg -v quiet -i '%s' -af volume=%d -f s16le -ar 48000 -ac 2 - | aplay -q -f S16_LE -r 48000 -c 2" % (url, vol),
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def set_volume(self, v):
        self.volume = max(MIN_VOL, min(MAX_VOL, v))
        return self.volume

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None


def draw(stdscr, stations, sel, playing_idx, player, status, offset):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    header = f" \U0001f4fb {countries[ci]['flag']} {countries[ci]['name']} "
    stdscr.addstr(0, 2, header, curses.A_REVERSE | curses.A_BOLD)
    vol = "MUTED" if player.muted else f"{player.volume}%"
    stdscr.addstr(0, max(0, w - 14), f" VOL {vol:>5} ", curses.A_REVERSE)

    rows = h - 3
    for i in range(rows):
        idx = offset + i
        if idx >= len(stations):
            break
        s = stations[idx]
        y = i + 1
        if s.get("sep"):
            stdscr.addstr(y, 2, "─" * min(60, w - 4), curses.A_DIM)
            continue
        playing = idx == playing_idx and player.running
        prefix = " ▶ " if playing else "   "
        line = f"{prefix}{s['freq']:>5}  {s['name']:<22} {s['genre']}"
        line = line[: w - 2]
        attr = curses.A_REVERSE | curses.A_BOLD if idx == sel else (
            curses.A_BOLD if playing else curses.A_NORMAL)
        stdscr.addstr(y, 0, line, attr)

    # footer
    bar_len = 30
    filled = int(bar_len * player.volume / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    footer = f" [{bar}] {vol:>5} "
    stdscr.addstr(h - 2, 2, footer[: w - 2], curses.A_DIM)
    stdscr.addstr(h - 1, 0, f" {status[: w - 2]}")


def main(stdscr):
    curses.curs_set(0)
    stdscr.timeout(500)  # refresh UI twice a second
    countries = load_data()
    ci = 0
    stations = grouped(countries[ci])
    selectable = [i for i, s in enumerate(stations) if not s.get("sep")]
    sel = 0
    offset = 0
    playing_idx = -1
    player = Player()
    status = "Enter to play · q quits"

    while True:
        draw(stdscr, stations, selectable[sel], playing_idx, player, status, offset)
        key = stdscr.getch()

        if key in (ord("q"), ord("Q")):
            player.stop()
            break
        elif key in (curses.KEY_UP, ord("k")):
            sel = max(0, sel - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            sel = min(len(selectable) - 1, sel + 1)
        elif key in (curses.KEY_PPAGE, ord("K")):
            sel = max(0, sel - 5)
        elif key in (curses.KEY_NPAGE, ord("J")):
            sel = min(len(selectable) - 1, sel + 5)
        elif key == ord(" "):
            player.stop()
            playing_idx = -1
            status = "Stopped"
        elif key == ord("r"):
            if playing_idx >= 0:
                s = stations[selectable[playing_idx]]
                player.play(s["url"])
                status = f"Reconnecting: {s['name']}…"
        elif key in (ord("+"), ord("=")):
            v = player.set_volume(player.volume + 5)
            status = f"Volume {v}%"
            if playing_idx >= 0:
                s = stations[selectable[playing_idx]]
                player.play(s["url"])
        elif key == ord("-"):
            v = player.set_volume(player.volume - 5)
            status = f"Volume {v}%"
            if playing_idx >= 0:
                s = stations[selectable[playing_idx]]
                player.play(s["url"])
        elif key == ord("m"):
            player.muted = not player.muted
            status = "Muted" if player.muted else f"Volume {player.volume}%"
            if playing_idx >= 0:
                s = stations[selectable[playing_idx]]
                player.play(s["url"])
        elif key == ord("c"):
            player.stop(); playing_idx = -1
            ci = (ci + 1) % len(countries)
            stations[:] = grouped(countries[ci])
            selectable[:] = [i for i, s in enumerate(stations) if not s.get("sep")]
            sel = 0; offset = 0
            status = f"Country: {countries[ci]['name']} ({len(selectable)} stations)"
        elif key in (curses.KEY_ENTER, 10, 13):
            idx = selectable[sel]
            s = stations[idx]
            player.play(s["url"])
            playing_idx = sel
            status = f"Playing {s['name']} ({s['freq']})…"

        # detect player death (network error etc.)
        if playing_idx >= 0 and not player.running:
            status = f"✗ Stream ended: {stations[selectable[playing_idx]]['name']}"
            playing_idx = -1

        # keep selection visible
        real_sel = selectable[sel]
        h, _ = stdscr.getmaxyx()
        rows = h - 3
        if real_sel < offset:
            offset = real_sel
        elif real_sel >= offset + rows:
            offset = real_sel - rows + 1


if __name__ == "__main__":
    if not os.path.exists(STATIONS_FILE):
        sys.exit("stations.json not found next to radio.py")
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
