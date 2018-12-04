#!python

from __future__ import print_function

import re
import struct

from codes import HIDCODES, SCANCODES


## Helper functions

def __create_scancode_names_dict():
    out = {}

    for k, v in SCANCODES.iteritems():
        if (k not in out) or (k in HIDCODES):
            out[k] = v

    return out


def __merge_dicts(*dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


## Constants

SCANCODE_NAMES = __create_scancode_names_dict()

KB_MOD_HID_MASK = {
    "KEY_LEFTCTRL": 0x01,
    "KEY_LEFTSHIFT": 0x02,
    "KEY_LEFTALT": 0x04,
    "KEY_LEFTMETA": 0x08,
    "KEY_RIGHTCTRL": 0x10,
    "KEY_RIGHTSHIFT": 0x20,
    "KEY_RIGHTALT": 0x40,
    "KEY_RIGHTMETA": 0x80,
}

# ASCII char to key name (not a complete list)
CHAR_KEYNAMES = __merge_dicts(
    {
        "`": "KEY_GRAVE",
        "-": "KEY_MINUS",
        "=": "KEY_EQUAL",

        "[": "KEY_LEFTBRACE",
        "]": "KEY_RIGHTBRACE",
        "\\": "KEY_BACKSLASH",

        "\t": "KEY_TAB",
        ";": "KEY_SEMICOLON",
        "'": "KEY_APOSTROPHE",
        "\n": "KEY_ENTER",

        ",": "KEY_COMMA",
        ".": "KEY_DOT",
        "/": "KEY_SLASH",

        " ": "KEY_SPACE",
    },

    # KEY_0 ~ KEY_9
    { str(x): "KEY_{}".format(x) for x in xrange(10) },

    # KEY_A ~ KEY_Z
    { chr(c): "KEY_{}".format(chr(c).upper()) for c in xrange(ord("a"), ord("z")+1) }
)

# ASCII char (with shift held) to key name (not a complete list)
SHIFT_CHAR_KEYNAMES = __merge_dicts(
    {
        "~": "KEY_GRAVE",
        "!": "KEY_1",
        "@": "KEY_2",
        "#": "KEY_3",
        "$": "KEY_4",
        "%": "KEY_5",
        "^": "KEY_6",
        "&": "KEY_7",
        "*": "KEY_8",
        "(": "KEY_9",
        ")": "KEY_0",
        "_": "KEY_MINUS",
        "+": "KEY_EQUAL",

        "{": "KEY_LEFTBRACE",
        "}": "KEY_RIGHTBRACE",
        "|": "KEY_BACKSLASH",

        ":": "KEY_SEMICOLON",
        '"': "KEY_APOSTROPHE",

        "<": "KEY_COMMA",
        ">": "KEY_DOT",
        "?": "KEY_SLASH",
    },

    # KEY_A ~ KEY_Z
    { chr(c): "KEY_{}".format(chr(c)) for c in xrange(ord("A"), ord("Z")+1) }
)


## Classes

class UnknownScancodeError(RuntimeError):
    pass

class UnknownCharecterError(RuntimeError):
    pass


## Functions

def _keyboard_report(keys, mods=None):
    if keys is None:
        keys = [0, 0, 0, 0, 0, 0]
    elif isinstance(keys, (int, basestring)):
        keys = [keys, 0, 0, 0, 0, 0]
    elif len(keys) < 6:
        keys = keys + (6-len(keys))*[0]
    elif len(keys) > 6:
        keys = keys[:6]

    for i, v in enumerate(keys):
        if isinstance(v, int):
            pass
        elif isinstance(v, basestring):
            keys[i] = HIDCODES[v]
        else:
            raise TypeError(
                "Expected keys to be ints or strings, got: '{}'".format(v))

    mod_byte = 0
    if mods is not None:
        if isinstance(mods, basestring):
            mod_byte = KB_MOD_HID_MASK[mods]
        else:
            for k in mods:
                mod_byte = mod_byte | KB_MOD_HID_MASK[k]

    return struct.pack("BBBBBBBB", mod_byte, 0, *keys)


# Scancode conversion

def scancode_to_key_name(scancode):
    try:
        return SCANCODE_NAMES[scancode]
    except KeyError:
        raise UnknownScancodeError("Unknown scancode: {}".format(scancode))


# Char conversion

def is_char_shifted(char):
    return char in SHIFT_CHAR_KEYNAMES


def char_to_key_name(char):
    if char in SHIFT_CHAR_KEYNAMES:
        return SHIFT_CHAR_KEYNAMES[char]
    elif char in CHAR_KEYNAMES:
        return CHAR_KEYNAMES[char]
    else:
        raise UnknownCharecterError("Unknown HID code for char: '{}'".format(char))


# Sending reports

def send(devpath, *args, **kwargs):
    report = _keyboard_report(*args, **kwargs)
    with open(devpath, "wb") as file:
        file.write(report)


def send_char(devpath, char):
    hidcode = HIDCODES[char_to_key_name(char)]

    if is_char_shifted(char):
        send(devpath, "KEY_LEFTSHIFT")
        send(devpath, [hidcode, "KEY_LEFTSHIFT"], "KEY_LEFTSHIFT")

    else:
        send(devpath, hidcode)

    send(devpath, None)


def send_string(devpath, s):
    for char in s:
        send_char(devpath, char)


## Main

if __name__ == "__main__":
    import argparse
    import sys

    default_devpath = "/dev/hidg0"

    parser = argparse.ArgumentParser(description="Send key presses to USB OTG")
    parser.add_argument("keys", type=str,
        help="String of keys to send")
    parser.add_argument("-d", "--device", type=str,
        default=default_devpath,
        help="Path of output device (default: '{}')".format(default_devpath))
    args = parser.parse_args()

    def print_stderr(*a, **kw):
        print(*a, file=sys.stderr, **kw)

    send_string(args.device, args.keys)
