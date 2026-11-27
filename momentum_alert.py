"""Research-only display flag; never changes eligibility, scores or ranking."""
import math

UPPER = 500.0
LOWER = -80.0
LABEL = "⚠ 極端動能"
EXPLANATION = "極端動能：12個月報酬 ≥ +500% 或 ≤ −80%；僅提醒查核原始價格、股息及公司行動，不代表資料錯誤，不改排名或交易規則。"


def is_extreme(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value >= UPPER or value <= LOWER))


def marker(value):
    return " " + LABEL if is_extreme(value) else ""
