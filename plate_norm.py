# -*- coding: utf-8 -*-
"""plate_norm.py -- licence plate normalisation helper (PLATE-NORM-001).

Licence plates are typed with mixed Cyrillic and Latin homoglyphs: '80 265 EA'
(Latin) and '80 265 ЕА' (Cyrillic Е and А) sit in adjacent rows of the
equipment reference, look identical on screen and never match byte-wise. Folding the
homoglyph set into one alphabet was required to reach 93.9% matching during
the 1C pilot (PILOT-1C-001), and the GPS plan-vs-fact track needs the same
fold to match Wialon object names against the equipment reference.

Target alphabet: LATIN. Chosen because the folded result is then pure ASCII
(digits plus Latin capitals), which is safe in every context this project is
constrained by -- cp1251 service logs, ASCII-only console output, file names,
URLs -- and matches how external systems (1C exports, Wialon object names)
most commonly type the letter part. The fold covers the twelve
uppercase pairs whose glyphs coincide:

    Cyrillic  А В Е К М Н О Р С Т У Х
    Latin     A B E K M H O P C T Y X

Input is upper-cased first, so lowercase homoglyphs fold too. All whitespace
and punctuation is stripped: only letters and digits survive. None and empty
input return an empty string.

IMPORTANT -- scope of this module (deliberate, do not "fix"):
this helper is NOT wired into any query yet, and it never rewrites a stored
value. Normalisation is applied at display and search time only, and that
wiring is a later task. Stored plates stay byte-for-byte what the operator
typed.
"""

# [REASON]: uppercase Cyrillic -> Latin homoglyph fold (PLATE-NORM-001).
# Only the twelve visually identical pairs are folded; other Cyrillic
# letters are not plate letters and pass through unchanged rather than
# being guessed at.
_CYR_TO_LAT = {
    'А': 'A',
    'В': 'B',
    'Е': 'E',
    'К': 'K',
    'М': 'M',
    'Н': 'H',
    'О': 'O',
    'Р': 'P',
    'С': 'C',
    'Т': 'T',
    'У': 'Y',
    'Х': 'X',
}
_TRANSLATE_TABLE = {ord(cyr): lat for cyr, lat in _CYR_TO_LAT.items()}


def normalize_plate(value):
    """Return the canonical comparison form of a licence plate.

    Upper-cases, folds Cyrillic homoglyphs to Latin, and strips everything
    that is not a letter or a digit (spaces, dots, dashes, NBSP, ...).
    None and empty input return ''.
    """
    if not value:
        return ''
    text = str(value).upper().translate(_TRANSLATE_TABLE)
    return ''.join(ch for ch in text if ch.isalnum())
