from roundlot.deskconfig import DeskConfig
from roundlot.presence import WARMUP_MS, OwnBook, evaluate
from roundlot.store import BUY, SELL, BookRow, SessionInfo

T0 = 1_000_000_000  # an arbitrary open, ms
FIRST = T0 + WARMUP_MS  # the last ineligible instant; seconds end at FIRST+1000, FIRST+2000, ...
D = 1_000_000  # $1 in micro-dollars


def rest(t, side, price, rem, old=None):
    return {"type": "order_state", "t": t, "sym": "X", "side": side, "price": price, "rem": rem, "state": "RESTING", "old_price": old}


def fill(t, side, price, rem):
    return {"type": "execution", "t": t, "sym": "X", "side": side, "order_price": price, "size": 1, "rem": rem, "liq": "MAKER"}


def cancel(t, side, price):
    return {"type": "cancelled", "t": t, "sym": "X", "side": side, "price": price, "size": 1, "reason": "CANCEL_REQUEST"}


def session(seconds=10, **kw):
    return SessionInfo(date="2026-11-02", open_time=T0, close_time=FIRST + seconds * 1000, **kw)


# a $300 stock: the price table says 40, the official lot is 100
BOOK = [BookRow(T0, 299 * D, 301 * D)]  # band $2
CONFIG = DeskConfig()


def test_own_book_amend_fill_cancel():
    b = OwnBook()
    b.apply(rest(1, BUY, 100, 80))
    b.apply(fill(2, BUY, 100, 30))
    assert b.best(BUY).remaining == 30
    b.apply(rest(3, BUY, 101, 80, old=100))  # amend to a new price
    assert (BUY, 100) not in b.orders and b.best(BUY).price == 101
    b.apply(cancel(4, BUY, 101))
    assert not b.has_side(BUY)


def test_stale_is_sticky_and_survives_an_amend():
    b = OwnBook()
    b.apply(rest(1, SELL, 300, 50))
    b.mark_stale(None, 300)  # the wall ask reaches our offer
    b.mark_stale(None, 305)  # moving back does not revive it
    assert b.has_side(SELL) and b.best(SELL) is None
    b.apply(rest(2, SELL, 302, 50, old=300))
    assert b.best(SELL) is None


def test_config_lot_below_official_fails_every_second():
    reports = [rest(T0 + 1000, BUY, int(299.5 * D), 80), rest(T0 + 1000, SELL, int(300.5 * D), 80)]
    r = evaluate("X", 100, session(), BOOK, reports, CONFIG)
    assert r.eligible_s == 10 and r.qualifying_s == 0
    assert r.lost["size_config_lot"] == 10 and r.size_only_s == 10
    assert r.historical_lot == 40 and r.median_shown == 80


def test_full_lot_qualifies_and_a_fill_below_it_is_depletion():
    reports = [
        rest(T0 + 1000, BUY, int(299.5 * D), 200),
        rest(T0 + 1000, SELL, int(300.5 * D), 200),
        fill(FIRST + 3500, BUY, int(299.5 * D), 20),  # seconds 4 and 5 short
        rest(FIRST + 5500, BUY, int(299.5 * D), 200, old=int(299.5 * D)),  # topped up
    ]
    r = evaluate("X", 100, session(), BOOK, reports, DeskConfig(size_overrides={"X": 100}))
    assert r.qualifying_s == 8 and r.lost["size_depleted"] == 2


def test_missing_side_spread_and_exclusions():
    reports = [rest(T0 + 1000, BUY, 299 * D + 10_000, 100)]  # bid only
    r = evaluate("X", 100, session(), BOOK, reports, CONFIG)
    assert r.lost["missing_side"] == 10

    wide = [rest(T0 + 1000, BUY, 299 * D + 10_000, 100), rest(T0 + 1000, SELL, 301 * D - 10_000, 100)]
    r = evaluate("X", 100, session(), BOOK, wide, DeskConfig(size_overrides={"X": 100}))
    assert r.lost["spread"] == 10  # $1.98 against a limit of $1.80

    gap = [{"instrument": "X", "start": FIRST + 2500, "end": FIRST + 4200}]
    r = evaluate("X", 100, session(feed_gaps=gap), BOOK, wide, CONFIG)
    assert r.eligible_s == 7  # seconds 3, 4 and 5 touch the gap

    r = evaluate("X", 100, session(), BOOK, wide, CONFIG, halt_time=FIRST + 6000)
    assert r.eligible_s == 6


def test_wall_through_the_quote_makes_it_stale():
    reports = [rest(T0 + 1000, BUY, int(299.5 * D), 100), rest(T0 + 1000, SELL, int(300.5 * D), 100)]
    books = BOOK + [BookRow(FIRST + 4100, int(299.6 * D), 301 * D)]
    r = evaluate("X", 100, session(), books, reports, CONFIG)
    assert r.qualifying_s == 4 and r.lost["stale"] == 6
