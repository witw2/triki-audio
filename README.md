# Triki Audio

Turn the **Triki** bottle-cap controller into a desk audio remote for Windows.

![Triki Audio](docs/screenshot.png)

| gesture | default action |
|---|---|
| **twist** the cap clockwise / counter-clockwise | volume up / down (with the Windows volume overlay) |
| **flip** it face up / face down | switch between two output devices (e.g. speakers ↔ headset) |
| **click** the button | mute / unmute the microphone |
| **double-tap the table** next to it | play / pause |
| double-click, hold the button | free to assign |

Every button and tap gesture can be set to: mute the mic, play/pause, next or
previous track, mute the speakers, switch output device, **any keyboard
shortcut**, or **open an app or website**.

It runs quietly in the system tray, can start with Windows, reconnects on its
own and shows a small "Muted" badge while your mic is off.

> Unofficial. Not affiliated with the maker of the Triki. Built on the
> reverse-engineered [triki-py](https://github.com/witw2/triki-py) library.

## Install

Requires Windows 10/11 and Python 3.9+.

```bash
git clone https://github.com/witw2/triki-audio.git
cd triki-audio
pip install -r requirements.txt
```

## Run

```bash
python triki_audio.py
```

or double-click **`Triki Audio.pyw`** (no console window).

First time:

1. **Device** tab → **Scan**, pick your Triki, **Connect**.
2. Keep the cap still for a second while it calibrates.
3. **Controls** tab → pick an output device for *face up* and *face down*.
4. **Actions** tab → choose what clicks and table taps do.

No cap at hand? **Device → Try demo** simulates one (your audio is left alone).

## Running in the background

* Closing the window hides it to the **tray** (the gold cap icon). Right-click
  the icon for quick controls or **Quit**.
* **Device → Start with Windows** starts it hidden in the tray when you sign in
  (a per-user startup entry, no admin rights needed).
* It remembers your Triki and reconnects automatically if it drops out.
* Starting it a second time just brings the running window to the front.
* Windows hides new tray icons in the **^** overflow - drag the cap onto the
  taskbar to keep it visible.

You can also start it hidden yourself: `python triki_audio.py --tray`.

## Tips

* **3D cap shows the wrong side up?** Toggle **Device → Sensor is upside down**.
* **Volume moves too fast or slow?** Controls → *Full sweep* sets how many
  degrees of twist go from 0 to 100 %.
* **Table taps too sensitive (or not enough)?** Actions → *Table taps* has a
  live meter: knock a few times and set *Knock needed* just below your knocks.
* **Pop-ups / muted badge:** Controls → *Notifications*. Drag the badge
  anywhere; it remembers the spot.

Settings are saved next to the app in `triki_audio_config.json`.

## Developing

If [triki-py](https://github.com/witw2/triki-py) is checked out next to this
folder (`../triki-py`), the app uses that copy when the library isn't
installed - handy for working on both at once.

```
triki_audio.py   app window, connection, gestures -> actions
actions.py       media keys, keyboard shortcuts, launching apps
cap_view.py      3D bottle-cap renderer and sensor charts
overlay.py       pop-up notification and the "Muted" badge
tray.py          system-tray icon and menu
system.py        start with Windows, single instance, taskbar identity
theme.py         colours, fonts, icons
```

## License

MIT
