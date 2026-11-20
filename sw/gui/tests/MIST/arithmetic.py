"""Bounded, seeded arithmetic generation; every answer is an integer 0–9.

Construct expressions backwards from their answer, without eval or unbounded
rejection loops. Parentheses make evaluation order explicit.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    expression: str
    answer: int
    difficulty: int


def generate_problem(rng, difficulty):
    answer = rng.randrange(10)
    if difficulty == 1:
        if rng.choice((True, False)):
            a = rng.randint(0, answer)
            expression = f"{a} + {answer - a}"
        else:
            a = rng.randint(answer, 9)
            expression = f"{a} − {a - answer}"
    elif difficulty == 2:
        total = rng.randint(answer, min(18, answer + 9))
        a = rng.randint(max(0, total - 9), min(9, total))
        expression = f"{a} + {total - a} − {total - answer}"
    elif difficulty == 3:
        a = rng.randint(2, 9)
        b = rng.randint(max(2, (answer + a - 1) // a), min(12, (99 + answer) // a))
        expression = f"{a} × {b} − {a * b - answer}"
    elif difficulty == 4:
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        c = rng.randint(max(0, answer - a * b), 9)
        expression = f"{a} × {b} + {c} − {a * b + c - answer}"
    elif difficulty == 5:
        divisor = rng.randint(2, 9)
        a = divisor * rng.randint(2, 9)
        factor = a // divisor
        b = rng.randint(max(2, (answer + factor - 1) // factor), min(12, (99 + answer) // factor))
        expression = f"({a} × {b}) ÷ {divisor} − {a * b // divisor - answer}"
    else:
        raise ValueError("difficulty must be between 1 and 5")
    return Problem(expression, answer, difficulty)
