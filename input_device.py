#!python

from __future__ import print_function
import time

import evdev


class InputEvent(object):
    '''
    Normalized input event wrapper around evdev.events.InputEvent
    '''

    KEYSTATE_UP = evdev.events.KeyEvent.key_up
    KEYSTATE_DOWN = evdev.events.KeyEvent.key_down
    KEYSTATE_HOLD = evdev.events.KeyEvent.key_hold

    def __init__(self, event, active_keys):
        self.code = event.code
        self.active_keys = active_keys

        self.is_key_event = False
        self.keyname = None
        self.keystate = None

        if event.type == evdev.ecodes.EV_KEY:
            self.is_key_event = True
            self.keyname = evdev.ecodes.keys[event.code]
            self.keystate = event.value

        #TODO: Mouse events


def read_loop(devpath, print_func=print):
    '''
    Wrapper for evdev.InputDevice.read_loop() to handle disconnects and manage
    key states
    '''

    while True:
        if print_func:
            print_func("Waiting for device '{}'...".format(devpath))

        # Get keyboard device
        dev = None
        while dev is None:
            try:
                dev = evdev.InputDevice(devpath)
                if print_func:
                    print_func("Connected to '{}'.".format(devpath))
            except:
                time.sleep(1)

        # Reserve exclusive access
        dev.grab()

        # List of keys held down, most recent key first
        # (Can't use dev.active_keys(), as it does not save sequence order)
        active_keys = []

        # Wrap read_loop()
        it = dev.read_loop()
        while True:
            try:
                ev = it.next()
            except IOError as e:
                # Device was disconnected
                if print_func:
                    print_func("Input device '{}' was closed: {}".format(devpath, e))
                break

            evdata = evdev.categorize(ev)

            if ev.type == evdev.ecodes.EV_KEY:
                # Track key states

                if evdata.keystate == evdata.key_down:
                    if evdata.scancode not in active_keys:
                        active_keys.insert(0, evdata.scancode)

                elif evdata.keystate == evdata.key_up:
                    if evdata.scancode in active_keys:
                        active_keys.pop(active_keys.index(evdata.scancode))

                #elif evdata.keystate == evdata.key_hold:
                #    # Key held
                #    pass

            #TODO: Mouse events

            yield InputEvent(ev, active_keys)


if __name__  == "__main__":
    import argparse
    import sys

    default_devpath = "/dev/input/event0"

    parser = argparse.ArgumentParser(description="Read keyboard input")
    parser.add_argument("-d", "--device", type=str,
        default=default_devpath,
        help="Path of input device (default: '{}')".format(default_devpath))
    args = parser.parse_args()

    def print_stderr(*a, **kw):
        print(*a, file=sys.stderr, **kw)

    print_stderr("Use ^C to quit")

    keystate_strings = {
        InputEvent.KEYSTATE_UP:   "up",
        InputEvent.KEYSTATE_DOWN: "down",
        InputEvent.KEYSTATE_HOLD: "hold",
    }

    try:
        for event in read_loop(args.device, print_func=print_stderr):
            if event.is_key_event:
                keystate_str = keystate_strings[event.keystate]

                print("{: <14} ({: >4}) {: <4}  Active: {}".format(
                    event.keyname, event.code, keystate_str, event.active_keys
                ))

    except KeyboardInterrupt:
        pass
