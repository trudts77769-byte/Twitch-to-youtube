#!/usr/bin/env python3
"""
Content library for Kids Brain Teaser Shorts (30 seconds, 9:16 vertical).

Puzzle types:
  - "odd_shape":  A grid of colored shapes; one is different.
  - "math":       Multiple-choice math problem.
  - "riddle":     Multiple-choice riddle.

Each puzzle dict:
  id, type, hook, instruction, bg (two hex colors), cta, plus type-specific data.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Shape-based odd-one-out puzzles
# ---------------------------------------------------------------------------
# Shapes are drawn procedurally — no emoji font required.
# shape kinds: "circle", "square", "triangle", "star", "heart", "diamond"
# ---------------------------------------------------------------------------

SHAPE_PUZZLES = [
    {
        "id": "shape-001-red-circles",
        "type": "odd_shape",
        "hook": "Can you find the odd one out?",
        "instruction": "Look closely!",
        "bg": ("FF6B9D", "C44569"),
        "shapes": ["circle"] * 15 + ["square"],
        "shape_colors": [(231, 76, 60)] * 15 + [(231, 76, 60)],  # same color
        "size": 130,
        "cols": 4,
        "answer_index": 15,
        "answer_explanation": "It's a square hiding among the circles!",
        "cta": "FOLLOW for more puzzles!",
    },
    {
        "id": "shape-002-green-stars",
        "type": "odd_shape",
        "hook": "One of these is NOT a star!",
        "instruction": "Can you spot it?",
        "bg": ("00B894", "00897B"),
        "shapes": ["star"] * 15 + ["heart"],
        "shape_colors": [(255, 215, 0)] * 16,  # all gold
        "size": 130,
        "cols": 4,
        "answer_index": 15,
        "answer_explanation": "A heart snuck in among the stars!",
        "cta": "LIKE if you found it fast!",
    },
    {
        "id": "shape-003-blue-diamonds",
        "type": "odd_shape",
        "hook": "Which diamond doesn't belong?",
        "instruction": "You have 10 seconds!",
        "bg": ("0984E3", "2D3436"),
        "shapes": ["diamond"] * 15 + ["diamond"],
        "shape_colors": [(116, 185, 255)] * 15 + [(255, 121, 198)],  # one pink
        "size": 130,
        "cols": 4,
        "answer_index": 15,
        "answer_explanation": "One diamond is pink instead of blue!",
        "cta": "COMMENT 'got it' if you did!",
    },
    {
        "id": "shape-004-triangles",
        "type": "odd_shape",
        "hook": "Can you spot the different shape?",
        "instruction": "Don't blink!",
        "bg": ("FDCB6E", "E17055"),
        "shapes": ["triangle"] * 8 + ["circle"] + ["triangle"] * 7,
        "shape_colors": [(108, 92, 231)] * 16,
        "size": 140,
        "cols": 4,
        "answer_index": 8,
        "answer_explanation": "A circle snuck into the triangle party!",
        "cta": "SUBSCRIBE for daily brain teasers!",
    },
    {
        "id": "shape-005-hearts",
        "type": "odd_shape",
        "hook": "Find the broken heart!",
        "instruction": "Hurry up!",
        "bg": ("E84393", "6C5CE7"),
        "shapes": ["heart"] * 10 + ["heart_broken"] + ["heart"] * 5,
        "shape_colors": [(255, 50, 80)] * 16,
        "size": 130,
        "cols": 4,
        "answer_index": 10,
        "answer_explanation": "Poor heart got cracked!",
        "cta": "FOLLOW for more!",
    },
    {
        "id": "shape-006-squares",
        "type": "odd_shape",
        "hook": "Which square is a different color?",
        "instruction": "Go!",
        "bg": ("2D3436", "636E72"),
        "shapes": ["square"] * 16,
        "shape_colors": [(0, 206, 201)] * 7 + [(253, 203, 110)] + [(0, 206, 201)] * 8,
        "size": 150,
        "cols": 4,
        "answer_index": 7,
        "answer_explanation": "That one yellow square gives it away!",
        "cta": "Did you get it in under 3 seconds?",
    },
]

# ---------------------------------------------------------------------------
# Math puzzles
# ---------------------------------------------------------------------------
MATH_PUZZLES = [
    {
        "id": "math-001-order",
        "type": "math",
        "hook": "Can you solve this in 10 seconds?",
        "instruction": "Remember the order of operations!",
        "problem": "15 + 27 × 0 + 45 = ?",
        "choices": ["87", "60", "45", "0"],
        "answer_index": 1,
        "answer_explanation": "Multiply first! 27×0=0. Then 15+0+45=60!",
        "cta": "Tricky, right? FOLLOW for more!",
        "bg": ("6C5CE7", "2D3436"),
    },
    {
        "id": "math-002-simple",
        "type": "math",
        "hook": "Math speed test!",
        "instruction": "You have 10 seconds!",
        "problem": "25 × 4 - 20 = ?",
        "choices": ["100", "80", "60", "120"],
        "answer_index": 1,
        "answer_explanation": "25×4=100, then 100-20=80!",
        "cta": "LIKE if you got it!",
        "bg": ("00B894", "00695C"),
    },
    {
        "id": "math-003-fruit",
        "type": "math",
        "hook": "Fruit math puzzle!",
        "instruction": "What's the answer?",
        "problem": "🍎=10  🍌=4  🍇=2\n🍇 + 🍎 + 🍌 = ?",
        "choices": ["14", "16", "12", "20"],
        "answer_index": 1,
        "answer_explanation": "2 + 10 + 4 = 16! Easy peasy!",
        "cta": "SUBSCRIBE for daily puzzles!",
        "bg": ("FD79A8", "E84393"),
    },
]

# ---------------------------------------------------------------------------
# Riddles
# ---------------------------------------------------------------------------
RIDDLE_PUZZLES = [
    {
        "id": "riddle-001-wet",
        "type": "riddle",
        "hook": "Riddle time! Think fast!",
        "instruction": "What gets wet while drying?",
        "problem": "What gets wet\nwhile drying?",
        "choices": ["A sponge", "A towel", "Rain", "A cloud"],
        "answer_index": 1,
        "answer_explanation": "A towel! It dries you but gets wet itself!",
        "cta": "FOLLOW for more riddles!",
        "bg": ("00CEC9", "0984E3"),
    },
    {
        "id": "riddle-002-silence",
        "type": "riddle",
        "hook": "Quick riddle! Can you guess?",
        "instruction": "Saying its name breaks it. What is it?",
        "problem": "Saying its name\nbreaks it.\nWhat is it?",
        "choices": ["Glass", "A secret", "Silence", "A bubble"],
        "answer_index": 2,
        "answer_explanation": "Silence! Saying 'silence' breaks the silence!",
        "cta": "LIKE if you got it right!",
        "bg": ("636E72", "2D3436"),
    },
    {
        "id": "riddle-003-foot",
        "type": "riddle",
        "hook": "Here's a tricky one!",
        "instruction": "What has a foot but no legs?",
        "problem": "What has a foot\nbut no legs?",
        "choices": ["A chair", "A snail", "A ruler", "A sock"],
        "answer_index": 2,
        "answer_explanation": "A ruler! 12 inches = 1 foot, but it has no legs!",
        "cta": "COMMENT your favorite riddle!",
        "bg": ("FDCB6E", "E17055"),
    },
]

ALL_PUZZLES = SHAPE_PUZZLES + MATH_PUZZLES + RIDDLE_PUZZLES


def get_puzzle(puzzle_id: str) -> dict | None:
    for p in ALL_PUZZLES:
        if p["id"] == puzzle_id:
            return p
    return None


def next_puzzle(uploaded_ids: set[str]) -> dict | None:
    for p in ALL_PUZZLES:
        if p["id"] not in uploaded_ids:
            return p
    return None


if __name__ == "__main__":
    print(f"Loaded {len(ALL_PUZZLES)} puzzles:")
    for p in ALL_PUZZLES:
        print(f"  - {p['id']} ({p['type']})")
