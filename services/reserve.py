# services/reserve.py
# 안전적립 / 쓸수있어 계산

def _to_int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default

def _to_rate(v, default=0.15) -> float:
    try:
        r = float(v)
    except (TypeError, ValueError):
        return default
    # 15 또는 0.15 둘 다 허용
    if r > 1:
        r = r / 100.0
    # 안전 범위 클램프
    return max(0.0, min(r, 0.95))

def preview(revenue, expense, reserve_rate=0.15) -> dict:
    """
    MVP: '지금 써도 되는 돈' 계산
    - profit = revenue - expense
    - reserve_amount = max(0, profit) * reserve_rate
    - safe_to_spend = profit - reserve_amount
    """
    rev = _to_int(revenue, 0)
    exp = _to_int(expense, 0)
    rate = _to_rate(reserve_rate, 0.15)

    profit = rev - exp
    reserve_amount = int(max(0, profit) * rate)
    safe_to_spend = profit - reserve_amount

    return {
        "revenue": rev,
        "expense": exp,
        "rate": rate,
        "profit": profit,
        "reserve_amount": reserve_amount,
        "safe_to_spend": safe_to_spend,
    }
