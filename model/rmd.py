"""IRS Required Minimum Distribution divisors (Uniform Lifetime Table)."""

from __future__ import annotations

# IRS Publication 590-B, Uniform Lifetime Table (ages 72–120+)
UNIFORM_LIFETIME_DIVISOR: dict[int, float] = {
    72: 27.4,
    73: 26.5,
    74: 25.5,
    75: 24.6,
    76: 23.7,
    77: 22.9,
    78: 22.0,
    79: 21.1,
    80: 20.2,
    81: 19.4,
    82: 18.5,
    83: 17.7,
    84: 16.8,
    85: 16.0,
    86: 15.2,
    87: 14.4,
    88: 13.7,
    89: 12.9,
    90: 12.2,
    91: 11.5,
    92: 10.8,
    93: 10.1,
    94: 9.5,
    95: 8.9,
    96: 8.4,
    97: 7.8,
    98: 7.3,
    99: 6.8,
    100: 6.4,
    101: 6.0,
    102: 5.6,
    103: 5.2,
    104: 4.9,
    105: 4.6,
    106: 4.3,
    107: 4.1,
    108: 3.9,
    109: 3.7,
    110: 3.5,
    111: 3.4,
    112: 3.3,
    113: 3.1,
    114: 3.0,
    115: 2.9,
    116: 2.8,
    117: 2.7,
    118: 2.5,
    119: 2.3,
    120: 2.0,
}


def uniform_lifetime_divisor(age: int) -> float:
    if age in UNIFORM_LIFETIME_DIVISOR:
        return UNIFORM_LIFETIME_DIVISOR[age]
    if age < 72:
        return 0.0
    return 2.0


def birth_year_for_person(person: dict, *, current_year: int) -> int:
    explicit = person.get("birth_year")
    if explicit not in (None, ""):
        return int(explicit)
    return int(current_year) - int(person.get("current_age", 40))


def rmd_start_age_for_birth_year(birth_year: int) -> int:
    """SECURE 2.0: age 75 if born 1960+, otherwise 73."""
    return 75 if birth_year >= 1960 else 73


def person_rmd_start_age(
    person: dict,
    *,
    current_year: int,
    default_start_age: int = 73,
    use_birth_year: bool = True,
) -> int:
    if not use_birth_year:
        return default_start_age
    return rmd_start_age_for_birth_year(birth_year_for_person(person, current_year=current_year))


def compute_rmd(traditional_balance: float, age: int, *, rmd_start_age: int = 73) -> float:
    """Minimum distribution from traditional tax-deferred balance (prior-year-end basis)."""
    if traditional_balance <= 0 or age < rmd_start_age:
        return 0.0
    divisor = uniform_lifetime_divisor(age)
    if divisor <= 0:
        return 0.0
    return traditional_balance / divisor


def household_rmd_age(
    ages: dict[str, int],
    rmd_start_ages: dict[str, int],
) -> int | None:
    """Oldest household member at/above their RMD start age (pooled traditional IRAs)."""
    eligible = [
        ages[name]
        for name in ages
        if ages[name] >= rmd_start_ages.get(name, 73)
    ]
    return max(eligible) if eligible else None