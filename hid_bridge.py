#!python

from __future__ import print_function
import re
import struct
import sys
import threading
import time
import traceback
from Queue import Queue

import evdev

import codes


## Constants

# Stop the program when handler raises an error
HALT_ON_ERROR = False


# Backup if evdev.ecodes.KEY is ambiguous
KB_KEYS = {v: k for k, v in codes.SCANCODES.iteritems() if k in codes.HIDCODES}

KB_MOD_HID_MASK = {
    codes.HIDCODES["KEY_LEFTCTRL"]: 0x01, # LCTRL
    codes.HIDCODES["KEY_LEFTSHIFT"]: 0x02, # LSHIFT
    codes.HIDCODES["KEY_LEFTALT"]: 0x04, # LALT
    codes.HIDCODES["KEY_LEFTMETA"]: 0x08, # LMETA
    codes.HIDCODES["KEY_RIGHTCTRL"]: 0x10, # RCTRL
    codes.HIDCODES["KEY_RIGHTSHIFT"]: 0x20, # RSHIFT
    codes.HIDCODES["KEY_RIGHTALT"]: 0x40, # RALT
    codes.HIDCODES["KEY_RIGHTMETA"]: 0x80, # RMETA
}


## Classes

class NoHidCodeError(RuntimeError):
    pass


class SimKeyEvent(object):
    def __init__(self, scancode, keystate):
        self.scancode = scancode
        self.keystate = keystate


## Debug Functions

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


## Pure Functions

def kb_hid_code(scancode):
    name = kb_key_name(scancode)
    try:
        return codes.HIDCODES[name]
    except KeyError:
        raise NoHidCodeError("No HID code for key '{}' ({})".format(name, scancode))


def kb_key_name(scancode, default=None):
    try:
        name = KB_KEYS[scancode]
        #name = evdev.ecodes.KEY[scancode]

        return name

    except KeyError:
        print("Unknown scancode: {}".format(scancode))
        return default


def or_values(d):
    out = 0
    for k, v in d.iteritems():
        out = out | v
    return out


def kb_report(state):
    if len(state["kb_keys"]) != 6:
        raise RuntimeError("'kb_keys' should be length 6: {}".format(kb_keys))

    return struct.pack("BBBBBBBB", or_values(state["kb_mods"]), 0, *state["kb_keys"])


def kb_state():
    return {
        # Keys currently pressed
        "kb_keys": [0] * 6,

        # Modifier keys
        "kb_mods": {k: 0 for k in KB_MOD_HID_MASK.keys()},

        # Input translation mode
        "input_translation": None
    }


## State Functions

def kb_sim_keypress(queue, *scancodes, **options):
    if "state" in options:
        state = options["state"]
    else:
        state = kb_state()

    for sc in scancodes:
        kbh_basic(queue, state, SimKeyEvent(sc, 1))
        kbh_basic(queue, state, SimKeyEvent(sc, 0))


def kbh_basic(queue, state, data):
    '''Basic keyboard key press handler'''

    hk = kb_hid_code(data.scancode)

    if data.keystate == 1:
        # Key down
        if hk in KB_MOD_HID_MASK:
            state["kb_mods"][hk] = KB_MOD_HID_MASK[hk]

        state["kb_keys"].pop()
        state["kb_keys"].insert(0, hk)

    elif data.keystate == 0:
        # Key up
        if hk in KB_MOD_HID_MASK:
            state["kb_mods"][hk] = 0

        if hk in state["kb_keys"]:
            state["kb_keys"].pop(state["kb_keys"].index(hk))
            state["kb_keys"].append(0)

    elif data.keystate == 2:
        # Key held
        pass

    #if data.keystate == 1:
    #    print(
    #        "Key: {}, Scan: {}, HID: {}, State: {}, Mods: 0x{:x}, Keys: {}".format(
    #            kb_key_name(data.scancode, "???"), data.scancode, hk,
    #            data.keystate, or_values(state["kb_mods"]), state["kb_keys"]
    #        )
    #    )

    queue.put(kb_report(state))


class WatchdogTimeout(RuntimeError):
    pass


class QuitInputMode(Exception):
    pass


class InputTranslator(object):
    LAYOUT_SWAP = "<swap>"

    @staticmethod
    def shift_layout_key(key):
        return "+{}".format(key)

    @classmethod
    def create_layout(cls, layout_map):
        char2keyname = {
            "a": cls.LAYOUT_SWAP,
            "b": "KEY_BACKSPACE",
            "c": "KEY_DELETE",  # Clear all
            "s": "KEY_SPACE",
            "e": "KEY_ENTER",
            "-": "KEY_MINUS",
            "'": "KEY_APOSTROPHE",
            "&": "+KEY_7",
            "#": "+KEY_3",
            "(": "+KEY_9",
            ")": "+KEY_0",
            "@": "+KEY_2",
            "!": "+KEY_1",
            "?": "+KEY_SLASH",
            ":": "+KEY_SEMICOLON",
            ".": "KEY_DOT",
            "_": "+KEY_MINUS",
            '"': "+KEY_APOSTROPHE",
            '/': "KEY_SLASH",
            ';': "KEY_SEMICOLON",
            '*': "+KEY_8",
            ',': "KEY_COMMA",
            "%": "+KEY_5",
            "$": "+KEY_4",
            "+": "+KEY_EQUAL",
        }

        out = {
            "rows": len(layout_map),
            "cols": 0
        }

        for y, row in enumerate(layout_map):
            for x, ch in enumerate(row):
                out["cols"] = max(out["cols"], len(row))

                pt = {
                    "x": x,
                    "y": y
                }
                if ch == " ":
                    # Unused button, ignore
                    pass
                elif ch in char2keyname:
                    keyname = char2keyname[ch]

                    if keyname.startswith("+"):
                        layout_key = cls.shift_layout_key(codes.SCANCODES.get(keyname[1:]))
                    else:
                        layout_key = codes.SCANCODES.get(keyname)

                    # If scancode doesn't exist, just use char2keyname value as key
                    out[layout_key or keyname] = pt

                elif re.match(r'[A-Z0-9]', ch):
                    sc = codes.SCANCODES["KEY_{}".format(ch)]

                    # Add lower and upper case keys
                    out[sc] = pt
                    out["+{}".format(sc)] = pt
                else:
                    print("Unknown layout char: {}".format(ch))
                    #raise RuntimeError("Unknown layout char: {}".format(ch))

        return out


    def __init__(self, queue):
        self.__watchdog = 0

        self._layout = None  # Must be assigned by derivative class
        self._layout_alt = {}

        self._quit_keys = [codes.SCANCODES[k] for k in
            ("KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_ESC")
        ]

        self._x = 0
        self._y = 0
        self._shift = False

        self._delay = 0.1

        self.init(queue)

    def _increment_watchdog(self):
        self.__watchdog += 1
        if self.__watchdog > 40:
            raise WatchdogTimeout(
                "Watchdog expired after {} keypresses".format(self.__watchdog))

        if (self._x < 0 or self._y < 0 or self._x >= self._layout["cols"]
                or self._y >= self._layout["rows"]):
            raise RuntimeError(
                "Position out of bounds: ({},{})".format(self._x, self._y))

    def _reset_watchdog(self):
        self.__watchdog = 0

    def menu_delay(self, delay=-1):
        if delay == -1:
            delay = self._delay
        if delay:
            time.sleep(delay)

    def menu_up(self, queue, bump=False, delay=-1):
        self._increment_watchdog()
        if not bump:
            self._y -= 1
        kb_sim_keypress(queue, codes.SCANCODES["KEY_UP"])
        self.menu_delay()

    def menu_down(self, queue, bump=False, delay=-1):
        self._increment_watchdog()
        if not bump:
            self._y += 1
        kb_sim_keypress(queue, codes.SCANCODES["KEY_DOWN"])
        self.menu_delay()

    def menu_left(self, queue, bump=False, delay=-1):
        self._increment_watchdog()
        if not bump:
            self._x -= 1
        kb_sim_keypress(queue, codes.SCANCODES["KEY_LEFT"])
        self.menu_delay()

    def menu_right(self, queue, bump=False, delay=-1):
        self._increment_watchdog()
        if not bump:
            self._x += 1
        kb_sim_keypress(queue, codes.SCANCODES["KEY_RIGHT"])
        self.menu_delay()

    def menu_goto(self, queue, target):
        tx = target["x"]
        ty = target["y"]

        # Move to row
        while self._y < ty:
            self.menu_down(queue)
        while self._y > ty:
            self.menu_up(queue)

        # Move to column
        while self._x < tx:
            self.menu_right(queue)
        while self._x > tx:
            self.menu_left(queue)

    def menu_select(self, queue, target=None):
        if target is not None:
            self.menu_goto(queue, target)

        self._increment_watchdog()
        self.menu_delay()
        kb_sim_keypress(queue, codes.SCANCODES["KEY_ENTER"])
        self.menu_delay(self._delay * 4)

    def init(self, queue):
        pass

    def input(self, queue, state, data):
        if data.keystate == 0:
            # Key-up

            if (data.scancode == codes.SCANCODES["KEY_LEFTSHIFT"]
                    or data.scancode == codes.SCANCODES["KEY_RIGHTSHIFT"]):
                # Shift key is released
                self._shift = False

        elif data.keystate == 1:
            # Key-down

            if (data.scancode == codes.SCANCODES["KEY_LEFTSHIFT"]
                    or data.scancode == codes.SCANCODES["KEY_RIGHTSHIFT"]):
                # Shift key is pressed
                self._shift = True

            elif data.scancode in self._quit_keys:
                # If any of these keys are used, then revert to default control
                kbh_basic(queue, state, data)
                raise QuitInputMode()

            else:
                self._reset_watchdog()

                if self._shift:
                    # Shift is pressed
                    layout_key = self.shift_layout_key(data.scancode)
                else:
                    layout_key = data.scancode

                if (layout_key not in self._layout) and (layout_key in self._layout_alt):
                    # Select keyboard swap key
                    self.menu_select(queue, self._layout[self.LAYOUT_SWAP])

                    # Swap layout maps
                    self._layout, self._layout_alt = self._layout_alt, self._layout

                if layout_key in self._layout:
                    self.menu_select(queue, self._layout[layout_key])

                    # Searching will change x,y position, so just give up
                    if data.scancode == codes.SCANCODES["KEY_ENTER"]:
                        raise QuitInputMode()

                else:
                    # Key is not in layout
                    print("Ignoring key: {} ({})".format(
                        layout_key, kb_key_name(data.scancode)))
                    #kbh_basic(queue, state, data)


class InputYoutube(InputTranslator):

    def init(self, queue):
        self._layout = self.create_layout([
            "ABCDEFGb",
            "HIJKLMNa",
            "OPQRSTU",
            "VWXYZ-'",
            "sce",
        ])

        self._layout_alt = self.create_layout([
            "123&#()b",
            "456@!?:a",
            '7890._"',
            "sc ",
        ])

        self._x = 7
        self._y = 0

        for x in xrange(10):
            self.menu_up(queue, True)
            self.menu_right(queue, True)

    def menu_goto(self, queue, target):
        tx = target["x"]
        ty = target["y"]

        #def debug_print_xy():
        #    print("({},{}) -> ({},{})".format(self._x, self._y, tx, ty))

        last_row = self._layout["rows"] - 1
        last_col = self._layout["cols"] - 1

        # If going to or from bottom row, always move to left side first
        if self._y != ty and (self._y == last_row or ty == last_row):
            while self._x > 0:
                self.menu_left(queue)

        # If coming from the right-most column, always move left first
        if self._x != tx and self._x == last_col:
            self.menu_left(queue)

        # Move to row
        while self._y < ty:
            self.menu_down(queue)
        while self._y > ty:
            self.menu_up(queue)

        # Add an extra movement for safety
        if self._y == 0:
            self.menu_up(queue, True)

        # Move to column
        while self._x < tx:
            self.menu_right(queue)
        while self._x > tx:
            self.menu_left(queue)

        # Add an extra movement for safety
        if self._x == last_col:
            self.menu_right(queue, True)


class InputHulu(InputTranslator):

    def init(self, queue):
        self._layout = self.create_layout([
            "asABCDEFGHIJKLMNOPQRSTUVWXYZb"
        ])

        self._layout_alt = self.create_layout([
            "as1234567890b"
        ])

        self._x = 15
        self._y = 0

    def menu_goto(self, queue, target):
        tx = target["x"]

        last_col = self._layout["cols"] - 1

        is_target_left = tx < self._x
        is_target_near = abs(tx - self._x) < (last_col + 1)*0.5

        while self._x != tx:
            if is_target_left != is_target_near:  # xor
                self.menu_right(queue)
            else:
                self.menu_left(queue)

            if self._x > last_col:
                self._x = 0
            if self._x < 0:
                self._x = last_col


class InputAmazonPrimeVideo(InputTranslator):

    def init(self, queue):
        self._layout = self.create_layout([
            "QWERTYUIOPb",
            "ASDFGHJKLac",
            "ZXCVBNM s  "
        ])

        self._layout_alt = self.create_layout([
            "1234567890b",
            '-/:;()*&"ac',
            ".,?!%$'@+# "
        ])

        self._x = 0
        self._y = 0

    def menu_goto(self, queue, target):
        tx = target["x"]
        ty = target["y"]

        #def debug_print_xy():
        #    print("({},{}) -> ({},{})".format(self._x, self._y, tx, ty))

        last_row = self._layout["rows"] - 1
        last_col = self._layout["cols"] - 1

        # Move to row
        while self._y < ty:
            self.menu_down(queue)

            # Account for long space bar (shift 1 to the left)
            if self._y == 2 and (self._x == 7 or self._x == 9):
                self._x -= 1

        while self._y > ty:
            self.menu_up(queue)

        # Move to column (wraps)
        is_target_left = tx < self._x
        is_target_near = abs(tx - self._x) < (last_col + 1)*0.5

        while self._x != tx:
            if is_target_left != is_target_near:  # xor
                self.menu_right(queue)

                # Account for long space bar
                if self._y == 2 and (self._x == 7 or self._x == 9):
                    self._x += 1
            else:
                self.menu_left(queue)

                # Account for long space bar
                if self._y == 2 and (self._x == 7 or self._x == 9):
                    self._x -= 1

            if self._x > last_col:
                self._x = 0
            if self._x < 0:
                self._x = last_col



def kbh_tv_menu(queue, state, data):

    if data.scancode == codes.SCANCODES["KEY_VOLUMEUP"]:
        if (data.keystate == 1):
            state["input_translation"] = None
            print("Input mode: Default")

    elif data.scancode == codes.SCANCODES["KEY_VOLUMEDOWN"]:
        if (data.keystate == 1):
            state["input_translation"] = InputYoutube(queue)
            print("Input mode: Youtube")

    elif data.scancode == codes.SCANCODES["KEY_MUTE"]:
        if (data.keystate == 1):
            state["input_translation"] = InputHulu(queue)
            print("Input mode: Hulu")

    elif data.scancode == codes.SCANCODES["KEY_NEXTSONG"]:
        if (data.keystate == 1):
            state["input_translation"] = InputAmazonPrimeVideo(queue)
            print("Input mode: AmazonPrimeVideo")

    else:

        if state["input_translation"] is None:
            kbh_basic(queue, state, data)

        else:
            try:
                state["input_translation"].input(queue, state, data)
            except QuitInputMode:
                state["input_translation"] = None

    #hk = kb_hid_code(data.scancode)

    #if data.keystate == 1 and hk == 69:
    #    # Press arrow keys in a circle
    #    kb_sim_keypress(queue, 108, 106, 103, 105)

    #else:
    #    kbh_basic(queue, state, data)


## System Functions

def dev_read_loop(devpath):
    '''Wrapper for evdev.InputDevice.read_loop() to handle disconnects'''

    # Get keyboard device
    print("Waiting for device '{}'...".format(devpath))

    dev = None
    while dev is None:
        try:
            dev = evdev.InputDevice(devpath)
            print("Connected to '{}'.".format(devpath))
        except:
            time.sleep(1)

    # Reserve exclusive access
    dev.grab()

    # Wrap read_loop()
    it = dev.read_loop()
    while True:
        try:
            yield it.next()
        except StopIteration:
            break
        except IOError as e:
            # Device was disconnected
            print("Input device '{}' was closed: {}".format(devpath, e))
            break


def loop_read_input_device(queue, devpath, handler):
    while True:
        state = kb_state()

        for event in dev_read_loop(devpath):
            if event.type == evdev.ecodes.EV_KEY:
                data = evdev.categorize(event)

                try:
                    handler(queue, state, data)
                except Exception as e:
                    print("Error in handler '{}': {}".format(handler.__name__, e))

                    if HALT_ON_ERROR:
                        queue.put(None)
                        raise
                    else:
                        eprint(traceback.format_exc())


def loop_write_usb_hid(queue, devpath):
    while True:
        report = queue.get()

        if report is None:
            print("Received 'None' in queue. Exiting thread.")
            break

        try:
            with open(devpath, 'wb') as fh:
                fh.write(report)
        except IOError as e:
            print("Dropped report for HID device '{}': {}".format(devpath, e))
        finally:
            queue.task_done()


def start_daemon(f, *args, **kwargs):
    #def f_close_on_error():
    #    try:
    #        f(*args, **kwargs)
    #    finally:
    #        sys.exit(1)

    #thread = threading.Thread(target=f_close_on_error)
    thread = threading.Thread(target=f, args=args, kwargs=kwargs)
    thread.daemon = True
    thread.start()
    return thread


## Main

if __name__ == "__main__":
    queue = Queue()

    start_daemon(loop_read_input_device, queue, '/dev/input/event0', kbh_tv_menu)

    loop_write_usb_hid(queue, '/dev/hidg0')
