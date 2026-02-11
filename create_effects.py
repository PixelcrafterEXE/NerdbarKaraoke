#!/usr/bin/env python3
"""Generate all effect configuration files"""

import json
import math
import os


def parse_linf(range_str):
    """Parse linear range like [0…200] or [-12…+12]"""
    parts = range_str.strip('[]').split('…')
    min_val = float(parts[0].replace('+', ''))
    max_val = float(parts[1].replace('+', ''))
    default = (min_val + max_val) / 2
    step = 1.0 if max_val - min_val > 50 else 0.1 if max_val - min_val > 10 else 0.01
    return min_val, max_val, default, step


def parse_logf(range_str):
    """Parse logarithmic range like [1k…20k] or [0.05…5]"""
    parts = range_str.strip('[]').split('…')
    min_val = parse_freq(parts[0])
    max_val = parse_freq(parts[1])
    # For log scale, use geometric mean for default
    if min_val > 0:
        default = math.sqrt(min_val * max_val)
    else:
        default = (min_val + max_val) / 2
    step = 0.01 if max_val < 100 else 1.0 if max_val < 1000 else 10.0
    return min_val, max_val, default, step


def parse_freq(s):
    """Parse frequency notation like '1k', '20k', '500'"""
    s = s.strip().replace('+', '')
    if 'k' in s.lower():
        return float(s.lower().replace('k', '')) * 1000
    return float(s)


def parse_enum(range_str):
    """Parse enum like [OFF, ON] - return count of options"""
    options = [x.strip() for x in range_str.strip('[]').split(',')]
    return 0.0, float(len(options) - 1), 0.0, 1.0


# All effects data with type numbers matching the mixer
EFFECTS = [
    ("hall", "Hall Reverb", 1, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[0.2…5]"),
        (3, "Size", "linf", "[2…100]"),
        (4, "Damping", "logf", "[1k…20k]"),
        (5, "Diffuse", "linf", "[1…30]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Bass Multi", "logf", "[0.5…2]"),
        (10, "Spread", "linf", "[0…50]"),
        (11, "Shape", "linf", "[0…250]"),
        (12, "Mod Speed", "linf", "[0…100]"),
    ]),
    ("plat", "Plate Reverb", 2, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[0.2…10]"),
        (3, "Size", "linf", "[2…100]"),
        (4, "Damping", "logf", "[1k…20k]"),
        (5, "Diffuse", "linf", "[1…30]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Bass Multi", "logf", "[0.5…2]"),
        (10, "Xover", "logf", "[10…500]"),
        (11, "Mod", "linf", "[0…50]"),
        (12, "Mod Speed", "linf", "[0…100]"),
    ]),
    ("ambi", "Ambiance Reverb", 3, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[0.2…7.3]"),
        (3, "Size", "linf", "[2…100]"),
        (4, "Damping", "logf", "[1k…20k]"),
        (5, "Diffuse", "linf", "[1…30]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Modulate", "linf", "[0…100]"),
        (10, "Tail Gain", "linf", "[0…100]"),
    ]),
    ("rplt", "Rich Plate Reverb", 4, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[0.3…29]"),
        (3, "Size", "linf", "[4…39]"),
        (4, "Damping", "logf", "[1k…20k]"),
        (5, "Diffuse", "linf", "[1…30]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Bass Multi", "logf", "[0.25…4]"),
        (10, "Spread", "linf", "[0…50]"),
        (11, "Attack", "linf", "[0…100]"),
        (12, "Spin", "linf", "[0…100]"),
        (13, "Echo L", "linf", "[0…1200]"),
        (14, "Echo R", "linf", "[0…1200]"),
        (15, "Echo Feed L", "linf", "[-100…+100]"),
        (16, "Echo Feed R", "linf", "[-100…+100]"),
    ]),
    ("room", "Room Reverb", 5, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[0.3…29]"),
        (3, "Size", "linf", "[4…72]"),
        (4, "Damping", "logf", "[1k…20k]"),
        (5, "Diffuse", "linf", "[1…30]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Bass Multi", "logf", "[0.25…4]"),
        (10, "Spread", "linf", "[0…50]"),
        (11, "Shape", "linf", "[0…250]"),
        (12, "Spin", "linf", "[0…100]"),
        (13, "Echo L", "linf", "[0…1200]"),
        (14, "Echo R", "linf", "[0…1200]"),
        (15, "Echo Feed L", "linf", "[-100…+100]"),
        (16, "Echo Feed R", "linf", "[-100…+100]"),
    ]),
    ("cham", "Chamber Reverb", 6, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[0.3…29]"),
        (3, "Size", "linf", "[4…72]"),
        (4, "Damping", "logf", "[1k…20k]"),
        (5, "Diffuse", "linf", "[1…30]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Bass Multi", "logf", "[0.25…4]"),
        (10, "Spread", "linf", "[0…50]"),
        (11, "Shape", "linf", "[0…250]"),
        (12, "Spin", "linf", "[0…100]"),
        (13, "Reflection L", "linf", "[0…500]"),
        (14, "Reflection R", "linf", "[0…500]"),
        (15, "Reflection Gain L", "linf", "[0…100]"),
        (16, "Reflection Gain R", "linf", "[0…100]"),
    ]),
    ("4tap", "4-Tap Delay", 7, [
        (1, "Time", "linf", "[1…3000]"),
        (2, "Gain Base", "linf", "[0…100]"),
        (3, "Feedback", "linf", "[0…100]"),
        (4, "Lo Cut", "logf", "[10…500]"),
        (5, "Hi Cut", "logf", "[200…20k]"),
        (6, "Spread", "linf", "[0…6]"),
        (7, "Factor A", "enum", "[1/4, 3/8, 1/2, 2/3, 1, 4/3, 3/2, 2, 3]"),
        (8, "Gain A", "linf", "[0…100]"),
        (9, "Factor B", "enum", "[1/4, 3/8, 1/2, 2/3, 1, 4/3, 3/2, 2, 3]"),
        (10, "Gain B", "linf", "[0…100]"),
        (11, "Factor C", "enum", "[1/4, 3/8, 1/2, 2/3, 1, 4/3, 3/2, 2, 3]"),
        (12, "Gain C", "linf", "[0…100]"),
        (13, "Cross Feed", "enum", "[OFF, ON]"),
        (14, "Mono", "enum", "[OFF, ON]"),
        (15, "Dry", "enum", "[OFF, ON]"),
    ]),
    ("vrev", "Vintage Reverb", 8, [
        (1, "Pre Delay", "linf", "[0…120]"),
        (2, "Decay", "logf", "[0.3…4.5]"),
        (3, "Modulate", "linf", "[0…10]"),
        (4, "Vintage", "enum", "[OFF, ON]"),
        (5, "Position", "enum", "[FRONT, REAR]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Lo Multiply", "logf", "[0.5…2]"),
        (10, "Hi Multiply", "logf", "[0.25…1]"),
    ]),
    ("vrm", "Vintage Room", 9, [
        (1, "Reverb Delay", "linf", "[0…20]"),
        (2, "Decay", "logf", "[0.1…20]"),
        (3, "Size", "linf", "[0…10]"),
        (4, "Density", "linf", "[1…30]"),
        (5, "ER Level", "linf", "[0…190]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Multiply", "logf", "[0.1…10]"),
        (8, "Hi Multiply", "logf", "[0.1…10]"),
        (9, "Lo Cut", "logf", "[10…500]"),
        (10, "Hi Cut", "logf", "[200…20k]"),
        (11, "ER Left", "linf", "[0…10]"),
        (12, "ER Right", "linf", "[0…10]"),
        (13, "Freeze", "enum", "[OFF, ON]"),
    ]),
    ("gate", "Gated Reverb", 10, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[140…1000]"),
        (3, "Attack", "linf", "[0…30]"),
        (4, "Density", "linf", "[1…30]"),
        (5, "Spread", "linf", "[0…100]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Hi Shv Gain", "linf", "[-30…0]"),
        (10, "Diffuse", "linf", "[1…30]"),
    ]),
    ("rvrs", "Reverse Reverb", 11, [
        (1, "Pre Delay", "linf", "[0…200]"),
        (2, "Decay", "logf", "[140…1000]"),
        (3, "Rise", "linf", "[0…50]"),
        (4, "Diffuse", "linf", "[1…30]"),
        (5, "Spread", "linf", "[1…100]"),
        (6, "Level", "linf", "[-12…+12]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Hi Shv Gain", "linf", "[-30…0]"),
    ]),
    ("dly", "Stereo Delay", 12, [
        (1, "Mix", "linf", "[0…100]"),
        (2, "Time", "linf", "[0…3000]"),
        (3, "Mode", "enum", "[ST, X, M]"),
        (4, "Factor L", "enum", "[1/4, 3/8, 1/2, 2/3, 1, 4/3, 3/2, 2, 3]"),
        (5, "Factor R", "enum", "[1/4, 3/8, 1/2, 2/3, 1, 4/3, 3/2, 2, 3]"),
        (6, "Offset L/R", "linf", "[-100…+100]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Feed Lo Cut", "logf", "[10…500]"),
        (10, "Feed Left", "linf", "[1…100]"),
        (11, "Feed Right", "linf", "[1…100]"),
        (12, "Feed Hi Cut", "logf", "[200…20k]"),
    ]),
    ("3tap", "3-Tap Delay", 13, [
        (1, "Dry", "linf", "[0…3000]"),
        (2, "Gain Base", "linf", "[0…100]"),
        (3, "Pan Base", "linf", "[-100…+100]"),
        (4, "Feedback", "linf", "[0…100]"),
        (5, "Lo Cut", "logf", "[10…500]"),
        (6, "Hi Cut", "logf", "[200…20k]"),
        (7, "Factor A", "enum", "[1/4, 3/8, 1/2, 2/3, 1, 4/3, 3/2, 2, 3]"),
        (8, "Gain A", "linf", "[0…100]"),
        (9, "Pan A", "linf", "[-100…+100]"),
        (10, "Factor B", "enum", "[1/4, 3/8, 1/2, 2/3, 1, 4/3, 3/2, 2, 3]"),
        (11, "Gain B", "linf", "[0…100]"),
        (12, "Pan B", "linf", "[-100…+100]"),
        (13, "Cross Feed", "enum", "[OFF, ON]"),
        (14, "Mono", "enum", "[OFF, ON]"),
        (15, "Dry", "enum", "[OFF, ON]"),
    ]),
    ("crs", "Stereo Chorus", 14, [
        (1, "Speed", "logf", "[0.05…5]"),
        (2, "Depth L", "linf", "[0…100]"),
        (3, "Depth R", "linf", "[0…100]"),
        (4, "Delay L", "logf", "[0.5…20]"),
        (5, "Delay R", "logf", "[0.5…20]"),
        (6, "Mix", "linf", "[0…100]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Phase", "linf", "[0…180]"),
        (10, "Wave", "linf", "[0…100]"),
        (11, "Spread", "linf", "[0…100]"),
    ]),
    ("flng", "Stereo Flanger", 15, [
        (1, "Speed", "logf", "[0.05…5]"),
        (2, "Depth L", "linf", "[0…100]"),
        (3, "Depth R", "linf", "[0…100]"),
        (4, "Delay L", "logf", "[0.5…20]"),
        (5, "Delay R", "logf", "[0.5…20]"),
        (6, "Mix", "linf", "[0…100]"),
        (7, "Lo Cut", "logf", "[10…500]"),
        (8, "Hi Cut", "logf", "[200…20k]"),
        (9, "Phase", "linf", "[0…180]"),
        (10, "Feed Lo Cut", "logf", "[10…500]"),
        (11, "Feed Hi Cut", "logf", "[200…20k]"),
        (12, "Feed", "linf", "[-90…+90]"),
    ]),
    ("phas", "Stereo Phaser", 16, [
        (1, "Speed", "logf", "[0.05…5]"),
        (2, "Depth", "linf", "[0…100]"),
        (3, "Resonance", "linf", "[0…80]"),
        (4, "Base", "linf", "[0…50]"),
        (5, "Stages", "linf", "[2…12]"),
        (6, "Mix", "linf", "[0…100]"),
        (7, "Wave", "linf", "[-50…+50]"),
        (8, "Phase", "linf", "[0…180]"),
        (9, "Env Modulation", "linf", "[-100…+100]"),
        (10, "Attack", "logf", "[10…1000]"),
        (11, "Hold", "logf", "[1…2000]"),
        (12, "Release", "logf", "[10…1000]"),
    ]),
    ("dimc", "Dimensional Chorus", 17, [
        (1, "Active", "enum", "[OFF, ON]"),
        (2, "Mode", "enum", "[M, ST]"),
        (3, "Dry", "enum", "[OFF, ON]"),
        (4, "Mode 1", "enum", "[OFF, ON]"),
        (5, "Mode 2", "enum", "[OFF, ON]"),
        (6, "Mode 3", "enum", "[OFF, ON]"),
        (7, "Mode 4", "enum", "[OFF, ON]"),
    ]),
    ("filt", "Mood Filter", 18, [
        (1, "Speed", "logf", "[0.05…20]"),
        (2, "Depth", "linf", "[0…100]"),
        (3, "Resonance", "linf", "[0…100]"),
        (4, "Base", "logf", "[10…15000]"),
        (5, "Mode", "enum", "[LP, HP, BP, NO]"),
        (6, "Mix", "linf", "[0…100]"),
        (7, "Wave", "enum", "[TRI, SIN, SAW, SAW-, RMP, SQU, RND]"),
        (8, "Phase", "linf", "[0…180]"),
        (9, "Env Modulation", "linf", "[-100…+100]"),
        (10, "Attack", "logf", "[10…250]"),
        (11, "Release", "logf", "[10…500]"),
        (12, "Drive", "linf", "[0…100]"),
        (13, "4 Pole", "enum", "[OFF, ON]"),
        (14, "Side Chain", "enum", "[OFF, ON]"),
    ]),
    ("rota", "Rotary Speaker", 19, [
        (1, "Lo Speed", "logf", "[0.1…4]"),
        (2, "Hi Speed", "logf", "[2…10]"),
        (3, "Accelerate", "linf", "[0…100]"),
        (4, "Distance", "linf", "[0…100]"),
        (5, "Balance", "linf", "[-100…+100]"),
        (6, "Mix", "linf", "[0…100]"),
        (7, "Stop", "enum", "[OFF, ON]"),
        (8, "Slow", "enum", "[OFF, ON]"),
    ]),
    ("pan", "Tremolo / Panner", 20, [
        (1, "Speed", "logf", "[0.05…4]"),
        (2, "Phase", "linf", "[0…180]"),
        (3, "Wave", "linf", "[-50…+50]"),
        (4, "Depth", "linf", "[0…100]"),
        (5, "Env Speed", "linf", "[0…100]"),
        (6, "Env Depth", "linf", "[0…100]"),
        (7, "Attack", "logf", "[10…1000]"),
        (8, "Hold", "logf", "[1…2000]"),
        (9, "Release", "logf", "[10…1000]"),
    ]),
    ("sub", "Sub Octaver", 21, [
        (1, "Active A", "enum", "[OFF, ON]"),
        (2, "Range A", "enum", "[LO, MID, HI]"),
        (3, "Dry A", "linf", "[0…100]"),
        (4, "Octave -1 A", "linf", "[0…100]"),
        (5, "Octave -2 A", "linf", "[0…100]"),
        (6, "Active B", "enum", "[OFF, ON]"),
        (7, "Range B", "enum", "[LO, MID, HI]"),
        (8, "Dry B", "linf", "[0…100]"),
        (9, "Octave -1 B", "linf", "[0…100]"),
        (10, "Octave -2 B", "linf", "[0…100]"),
    ]),
    ("d_rv", "Delay / Chamber", 22, [
        (1, "Time", "linf", "[1…3000]"),
        (2, "Pattern", "enum", "[1/4, 1/3, 3/8, 1/2, 2/3, 3/4, 1, 1/4X, 1/3X, 3/8X, 1/2X, 2/3X, 3/4X, 1X]"),
        (3, "Feed Hi Cut", "logf", "[1000…20000]"),
        (4, "Feedback", "linf", "[0…100]"),
        (5, "Cross Feed", "linf", "[0…100]"),
        (6, "Balance", "linf", "[-100…+100]"),
        (7, "Pre Delay", "linf", "[0…200]"),
        (8, "Decay", "logf", "[0.1…5]"),
        (9, "Size", "linf", "[2…100]"),
        (10, "Damping", "logf", "[1000…20000]"),
        (11, "Lo Cut", "logf", "[10…500]"),
        (12, "Mix", "linf", "[0…100]"),
    ]),
    ("d_cr", "Delay / Chorus", 23, [
        (1, "Time", "linf", "[1…3000]"),
        (2, "Pattern", "enum", "[1/4, 1/3, 3/8, 1/2, 2/3, 3/4, 1, 1/4X, 1/3X, 3/8X, 1/2X, 2/3X, 3/4X, 1X]"),
        (3, "Feed Hi Cut", "logf", "[1000…20000]"),
        (4, "Feedback", "linf", "[0…100]"),
        (5, "Cross Feed", "linf", "[0…100]"),
        (6, "Balance", "linf", "[-100…+100]"),
        (7, "Speed", "logf", "[0.05…4]"),
        (8, "Depth", "linf", "[0…100]"),
        (9, "Delay", "logf", "[0.5…50]"),
        (10, "Phase", "linf", "[0…180]"),
        (11, "Wave", "linf", "[0…100]"),
        (12, "Mix", "linf", "[0…100]"),
    ]),
    ("d_fl", "Delay / Flanger", 24, [
        (1, "Time", "linf", "[1…3000]"),
        (2, "Pattern", "enum", "[1/4, 1/3, 3/8, 1/2, 2/3, 3/4, 1, 1/4X, 1/3X, 3/8X, 1/2X, 2/3X, 3/4X, 1X]"),
        (3, "Feed Hi Cut", "logf", "[1000…20000]"),
        (4, "Feedback", "linf", "[0…100]"),
        (5, "Cross Feed", "linf", "[0…100]"),
        (6, "Balance", "linf", "[-100…+100]"),
        (7, "Speed", "logf", "[0.05…4]"),
        (8, "Depth", "linf", "[0…100]"),
        (9, "Delay", "logf", "[0.5…20]"),
        (10, "Phase", "linf", "[0…180]"),
        (11, "Feed", "linf", "[-90…+90]"),
        (12, "Mix", "linf", "[0…100]"),
    ]),
    ("cr_r", "Chorus / Chamber", 25, [
        (1, "Speed", "logf", "[0.05…4]"),
        (2, "Depth", "linf", "[0…100]"),
        (3, "Delay", "logf", "[0.5…50]"),
        (4, "Phase", "linf", "[0…180]"),
        (5, "Wave", "linf", "[0…100]"),
        (6, "Balance", "linf", "[-100…+100]"),
        (7, "Pre Delay", "linf", "[0…200]"),
        (8, "Decay", "logf", "[0.1…5]"),
        (9, "Size", "linf", "[2…100]"),
        (10, "Damping", "logf", "[1k…20k]"),
        (11, "Lo Cut", "logf", "[10…500]"),
        (12, "Mix", "linf", "[0…100]"),
    ]),
    ("fl_r", "Flanger / Chamber", 26, [
        (1, "Speed", "logf", "[0.05…4]"),
        (2, "Depth", "linf", "[0…100]"),
        (3, "Delay", "logf", "[0.5…20]"),
        (4, "Phase", "linf", "[0…180]"),
        (5, "Feed", "linf", "[-90…+90]"),
        (6, "Balance", "linf", "[-100…+100]"),
        (7, "Pre Delay", "linf", "[0…200]"),
        (8, "Decay", "logf", "[0.1…5]"),
        (9, "Size", "linf", "[2…100]"),
        (10, "Damping", "logf", "[1k…20k]"),
        (11, "Lo Cut", "logf", "[10…500]"),
        (12, "Mix", "linf", "[0…100]"),
    ]),
    ("modd", "Modulation Delay", 27, [
        (1, "Time", "linf", "[1…3000]"),
        (2, "Delay", "enum", "[1, 1/2, 2/3, 3/2]"),
        (3, "Feed", "linf", "[0…100]"),
        (4, "Lo Cut", "logf", "[10…500]"),
        (5, "Hi Cut", "logf", "[200…20k]"),
        (6, "Depth Rate", "linf", "[0…100]"),
        (7, "Rate", "logf", "[0.05…10]"),
        (8, "Setup", "enum", "[PAR, SER]"),
        (9, "Type", "enum", "[AMB, CLUB, HALL]"),
        (10, "Decay", "linf", "[1…10]"),
        (11, "Damping", "logf", "[1k…20k]"),
        (12, "Balance", "linf", "[-100…+100]"),
        (13, "Mix", "linf", "[0…100]"),
    ]),
]


def create_effect_file(effects_dir, effect_id, name, type_num, params):
    """Create a single effect configuration file"""
    parameters = []
    for idx, param_name, scale_type, range_str in params:
        if scale_type == "linf":
            min_v, max_v, default_v, step_v = parse_linf(range_str)
        elif scale_type == "logf":
            min_v, max_v, default_v, step_v = parse_logf(range_str)
        elif scale_type == "enum":
            min_v, max_v, default_v, step_v = parse_enum(range_str)
        else:
            continue
        
        parameters.append({
            "index": idx,
            "name": param_name,
            "default": round(default_v, 2),
            "min": round(min_v, 2),
            "max": round(max_v, 2),
            "step": round(step_v, 2)
        })
    
    effect_config = {
        "id": effect_id,
        "name": name,
        "type": type_num,
        "visible": True,
        "user_editable": {},
        "parameters": parameters
    }
    
    filepath = os.path.join(effects_dir, f"{effect_id}.json")
    with open(filepath, 'w') as f:
        json.dump(effect_config, f, indent=2)
    return filepath


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate effect JSON files")
    parser.add_argument("--out", "-o", help="Output directory for effect files", default=os.path.join(os.getcwd(), "data", "effects"))
    args = parser.parse_args()

    effects_dir = args.out
    os.makedirs(effects_dir, exist_ok=True)

    print(f"Creating {len(EFFECTS)} effect configuration files in {effects_dir}...")
    for effect_id, name, type_num, params in EFFECTS:
        filepath = create_effect_file(effects_dir, effect_id, name, type_num, params)
        print(f"  ✓ {effect_id}.json ({len(params)} parameters)")

    print(f"\n✅ All {len(EFFECTS)} effect files created successfully!")


if __name__ == "__main__":
    main()
