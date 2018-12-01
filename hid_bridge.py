#!python

from __future__ import print_function
import struct
import threading
import time
import traceback
from Queue import Queue
import sys

import evdev

import codes


## Constants

# Stop the program when handler raises an error
HALT_ON_ERROR = True


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
        self.scancode = scancode,
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
        raise NoHidCodeError("No HID code for {} ({})".format(name, scancode))


def kb_key_name(scancode, default=None):
    try:
        name = evdev.ecodes.KEY[scancode]

        if not isinstance(name, str):
            print("Ambiguous scancode {}: {}".format(scancode, name))
            name = KB_KEYS[scancode]

        return name

    except KeyError:
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
        "kb_mods": {k: 0 for k in KB_MOD_HID_MASK.keys()}
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
            print(state["kb_mods"])

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

    if data.keystate != 2:
        print(
            "Key: {}, Scan: {}, HID: {}, State: {}, Mods: 0x{:x}, Keys: {}".format(
                kb_key_name(data.scancode, "???"), data.scancode, hk,
                data.keystate, or_values(state["kb_mods"]), state["kb_keys"]
            )
        )

    queue.put(kb_report(state))


def kbh_test(queue, state, data):

    hk = kb_hid_code(data.scancode)

    if data.keystate == 1 and hk == 69:
        # Press arrow keys in a circle
        kb_sim_keypress(queue, 108, 106, 103, 105)

    else:
        kbh_basic(queue, state, data)


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

    start_daemon(loop_read_input_device, queue, '/dev/input/event0', kbh_test)

    loop_write_usb_hid(queue, '/dev/hidg0')
