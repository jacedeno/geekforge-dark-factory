"""Verifier browser probes for stage-2 (Playwright, headless Chromium), from plans/stage-2/brief.md.

PROBE_BASE_URL = stage-2 service. PROBE_SHOTS = directory for screenshots (visual review).
Elements are found by data-testid only.
"""
from __future__ import annotations

import os
import re
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "stage-1", "probes"))
from test_probes import ADA, BOB, BASE, day, fixture, imp, key, login, ok, reset, restaurant  # noqa: E402

SHOTS = os.environ.get("PROBE_SHOTS")
TABLES = [{"id": "t_1", "label": "Window", "capacity": 2}, {"id": "t_2", "label": "Garden", "capacity": 4},
          {"id": "t_3", "label": "Terrace", "capacity": 6}, {"id": "t_4", "label": "Bar", "capacity": 2}]


def ui_restaurant(**kw):
    r = restaurant(tables=[dict(t) for t in TABLES], **kw)
    r["combinable"] = [["t_1", "t_2"], ["t_3", "t_2"]]
    return r


def sel(name):
    return f"[data-testid='{name}']"


@pytest.fixture(scope="session")
def browser():
    from playwright import sync_api
    with sync_api.sync_playwright() as drv:
        b = drv.chromium.launch(channel="chromium")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(base_url=BASE)
    ctx.set_default_timeout(10_000)
    p = ctx.new_page()
    p.keys = []  # idempotency keys of POST /reservations, in order
    p.on("request", lambda r: p.keys.append(r.headers.get("idempotency-key"))
         if r.method == "POST" and r.url.split("?")[0].endswith("/reservations") else None)
    yield p
    ctx.close()


@pytest.fixture
def seeded():
    reset(fixture(restaurants=[ui_restaurant()]))


def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"{name}.png"), full_page=True)


def log_in(page, user=ADA):
    page.goto("/login")
    page.fill(sel("login-email"), user["email"])
    page.fill(sel("login-password"), user["password"])
    page.click(sel("login-submit"))
    page.wait_for_selector(sel("current-user"))


def start_search(page, party=4, date=None, goto=True):
    if goto:
        page.goto("/")
    page.select_option(sel("restaurant-select"), "r_anker")
    page.fill(sel("date-input"), date or day())
    page.fill(sel("party-size-input"), str(party))
    page.click(sel("search-button"))


def search(page, party=4, date=None, goto=True):
    start_search(page, party, date, goto)
    page.wait_for_selector(f"{sel('availability-grid')}, {sel('no-slots')}")


def avail(party, date=None):
    r = httpx.get(f"{BASE}/availability", params={"restaurant_id": "r_anker", "date": date or day(),
                                                  "party_size": party})
    return ok(r, 200)["slots"]


def cell_state(page, tid):
    return page.get_attribute(sel(tid), "data-available")


def api_list(user=ADA):
    return ok(login(user).get("/reservations"), 200)["reservations"]


def open_form(page, cell="slot-t_2-19:00", party=4):
    search(page, party)
    page.click(sel(cell))
    page.wait_for_selector(sel("booking-form"))


def wait_gone(page, tid, timeout=5000):
    page.wait_for_selector(sel(tid), state="detached", timeout=timeout)


def present(page, tid):
    return page.locator(sel(tid)).count() > 0 and page.locator(sel(tid)).first.is_visible()


# ---------------------------------------------------------------- auth / session

def test_current_user_on_every_route(seeded, page):
    log_in(page)
    for route in ("/", "/signup", "/login", "/lookup"):
        page.goto(route)
        page.wait_for_selector(sel("current-user"))
        assert "Ada" in page.text_content(sel("current-user")), route
    shot(page, "lookup-signed-in")


def test_logout_forgets_token(seeded, page):
    log_in(page)
    page.click(sel("logout-button"))
    wait_gone(page, "current-user")
    for route in ("/", "/lookup"):
        page.goto(route)
        page.wait_for_timeout(300)
        assert page.locator(sel("current-user")).count() == 0


def test_signup_errors_and_auth_error_absent(seeded, page):
    page.goto("/signup")
    assert page.locator(sel("auth-error")).count() == 0
    page.fill(sel("signup-email"), ADA["email"])
    page.fill(sel("signup-password"), "correct horse")
    page.fill(sel("signup-display-name"), "Ada2")
    page.click(sel("signup-submit"))
    page.wait_for_selector(sel("auth-error"))
    assert page.text_content(sel("auth-error")).strip()
    shot(page, "signup-error")
    page.fill(sel("signup-email"), "short@x.io")
    page.fill(sel("signup-password"), "1234567")
    page.click(sel("signup-submit"))
    page.wait_for_selector(sel("auth-error"))
    assert page.locator(sel("current-user")).count() == 0


# ---------------------------------------------------------------- grid

def test_grid_matches_api(seeded, page):
    login(BOB).post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_3",
                                            "starts_at_local": f"{day()}T19:00", "party_size": 2}, key_=key())
    search(page, party=3, goto=True)
    shot(page, "grid-signed-out-desktop")
    for s in avail(3):
        hhmm = s["starts_at_local"][-5:]
        for t in TABLES:
            want = "true" if t["id"] in s["available_table_ids"] else "false"
            assert cell_state(page, f"slot-{t['id']}-{hhmm}") == want, (t["id"], hhmm)


def test_combination_cells(seeded, page):
    search(page, party=5)
    slots = avail(5)
    for s in slots:
        hhmm = s["starts_at_local"][-5:]
        opts = [o["table_ids"] for o in s["available_options"]]
        for pair in (["t_1", "t_2"], ["t_3", "t_2"]):
            tid = f"slot-{pair[0]}+{pair[1]}-{hhmm}"
            assert cell_state(page, tid) == ("true" if pair in opts else "false"), tid
    # reversed id order never used
    assert page.locator(sel(f"slot-t_2+t_1-19:00")).count() == 0
    # human labels, not raw ids
    txt = page.inner_text(sel("slot-t_1+t_2-19:00"))
    assert "Window" in txt and "Garden" in txt and "t_1" not in txt, txt
    # party 7: t_1+t_2 (6) too small -> absent; t_3+t_2 (10) present
    search(page, party=7)
    assert page.locator(sel("slot-t_1+t_2-19:00")).count() == 0
    assert page.locator(sel("slot-t_3+t_2-19:00")).count() == 1
    # booked member -> pair cell present but unavailable
    login(BOB).post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_2",
                                            "starts_at_local": f"{day()}T19:00", "party_size": 2}, key_=key())
    search(page, party=5)
    assert cell_state(page, "slot-t_1+t_2-19:00") == "false"
    assert cell_state(page, "slot-t_3+t_2-20:00") == "false"
    assert cell_state(page, "slot-t_3+t_2-21:00") == "true"


def test_signed_out_click(seeded, page):
    search(page, party=2)
    page.click(sel("slot-t_2-19:00"))
    page.wait_for_timeout(500)
    if page.url.rstrip("/").endswith("/login"):
        return  # spec allows navigating to /login
    page.wait_for_selector(sel("auth-error"))
    assert page.locator(f"{sel('auth-error')} a[href*='login']").count() >= 1, "E4: link to /login"
    assert page.locator(sel("availability-grid")).count() == 1
    assert page.locator(sel("booking-form")).count() == 0 or not page.is_visible(sel("booking-form"))
    shot(page, "signed-out-click")


def test_unavailable_click_does_nothing(seeded, page):
    log_in(page)
    search(page, party=5)
    page.click(sel("slot-t_1-19:00"), force=True)
    page.wait_for_timeout(400)
    assert page.locator(sel("booking-form")).count() == 0 or not page.is_visible(sel("booking-form"))


def test_out_of_order_search(seeded, page):
    held = []

    def handler(route):
        if not held:
            held.append(route)  # hold search A
        else:
            route.continue_()
    page.goto("/")
    page.route(re.compile(r".*/availability\?.*"), handler)
    start_search(page, party=2, goto=False)       # A: party 2 -> t_1 available
    page.wait_for_timeout(300)
    start_search(page, party=6, goto=False)       # B: party 6 -> only t_3
    page.wait_for_selector(sel("slot-t_1-19:00"))
    page.wait_for_function("document.querySelector(\"[data-testid='slot-t_1-19:00']\")"
                           ".getAttribute('data-available') === 'false'")
    held[0].continue_()
    page.wait_for_timeout(1500)
    assert cell_state(page, "slot-t_1-19:00") == "false", "late response of A was applied"
    assert cell_state(page, "slot-t_3-19:00") == "true"
    assert page.input_value(sel("party-size-input")) == "6"


def test_new_search_closes_old_form(seeded, page):
    log_in(page)
    open_form(page, party=4)
    search(page, party=6, goto=False)
    page.wait_for_timeout(300)
    if page.locator(sel("booking-form")).count() and page.is_visible(sel("booking-form")):
        assert page.input_value(sel("booking-party-size")) == "6", "a visible form must describe the latest search"


# ---------------------------------------------------------------- booking

def test_pair_booking_flow(seeded, page):
    log_in(page)
    open_form(page, cell="slot-t_1+t_2-19:00", party=5)
    summary = page.text_content(sel("booking-summary"))
    assert "Window" in summary and "Garden" in summary and "19:00" in summary, summary
    assert page.input_value(sel("booking-party-size")) == "5"
    shot(page, "booking-form-pair")
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation"))
    ref = page.text_content(sel("confirmation-reference"))
    assert re.fullmatch(r"[A-Z0-9]{6,12}", ref), repr(ref)
    tables = page.text_content(sel("confirmation-tables"))
    details = page.text_content(sel("confirmation-details"))
    assert "Window" in tables and "Garden" in tables, tables
    for part in ("Zum Anker", "Window", "Garden", "19:00"):
        assert part in details, (part, details)
    assert page.is_visible(sel("booking-form")), "form stays after success"
    shot(page, "confirmation-pair")
    [r] = api_list()
    assert r["reference"] == ref and r["table_ids"] == ["t_1", "t_2"] and r["party_size"] == 5
    page.goto("/lookup")
    page.fill(sel("lookup-reference-input"), ref)
    page.click(sel("lookup-submit"))
    page.wait_for_selector(sel("reservation-detail"))
    rt = page.text_content(sel("reservation-tables"))
    assert "Window" in rt and "Garden" in rt, rt
    shot(page, "lookup-pair")


def test_resubmit_same_reference(seeded, page):
    log_in(page)
    open_form(page)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation-reference"))
    ref = page.text_content(sel("confirmation-reference"))
    page.click(sel("booking-submit"))
    page.wait_for_timeout(800)
    assert page.text_content(sel("confirmation-reference")) == ref
    assert page.locator(sel("booking-error")).count() == 0
    assert len(api_list()) == 1
    assert len(page.keys) == 2 and page.keys[0] == page.keys[1] and page.keys[0]


def test_changed_field_new_booking(seeded, page):
    log_in(page)
    open_form(page, cell="slot-t_3-19:00", party=4)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation-reference"))
    ref1 = page.text_content(sel("confirmation-reference"))
    page.fill(sel("booking-party-size"), "3")
    page.click(sel("booking-submit"))
    # t_3 is now taken by the first booking -> this must be a NEW request, refused with 409
    page.wait_for_timeout(1000)
    assert page.keys[0] != page.keys[-1], "changed field must use a new key"
    assert len(api_list()) == 1
    assert page.locator(sel("booking-error")).count() == 1
    assert not (page.locator(sel("confirmation-reference")).count() and
                page.text_content(sel("confirmation-reference")) != ref1)


def test_changed_field_books_again_elsewhere(seeded, page):
    log_in(page)
    open_form(page, cell="slot-t_3-19:00", party=4)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation-reference"))
    ref1 = page.text_content(sel("confirmation-reference"))
    search(page, party=4, goto=False)
    page.click(sel("slot-t_3-21:00"))
    page.wait_for_selector(sel("booking-form"))
    page.click(sel("booking-submit"))
    page.wait_for_function(f"(document.querySelector(\"[data-testid='confirmation-reference']\")||{{}})"
                           f".textContent && document.querySelector(\"[data-testid='confirmation-reference']\")"
                           f".textContent !== '{ref1}'")
    assert len(api_list()) == 2


def test_conflict_refreshes_and_keeps_form(seeded, page):
    log_in(page)
    open_form(page, cell="slot-t_2-19:00", party=3)
    page.fill(sel("booking-party-size"), "4")
    login(BOB).post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_2",
                                            "starts_at_local": f"{day()}T19:00", "party_size": 2}, key_=key())
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-error"))
    assert page.text_content(sel("booking-error")).strip()
    page.wait_for_function("document.querySelector(\"[data-testid='slot-t_2-19:00']\")"
                           ".getAttribute('data-available') === 'false'", timeout=5000)
    assert page.is_visible(sel("booking-form"))
    assert page.input_value(sel("booking-party-size")) == "4"
    assert "Garden" in page.text_content(sel("booking-summary"))
    assert page.locator(sel("confirmation")).count() == 0 or not page.is_visible(sel("confirmation"))
    assert page.locator(sel("booking-uncertain")).count() == 0
    shot(page, "conflict")


def lose_after_commit(page, store):
    def handler(route):
        if route.request.method == "POST" and not store:
            resp = route.fetch()
            store.append(resp.json())
            route.abort("connectionreset")
        else:
            route.continue_()
    page.route(re.compile(r".*/reservations$"), handler)


def test_lost_response_recovers(seeded, page):
    log_in(page)
    open_form(page, cell="slot-t_1+t_2-19:00", party=5)
    store = []
    lose_after_commit(page, store)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-uncertain"))
    assert page.text_content(sel("booking-uncertain")).strip()
    assert page.locator(sel("booking-error")).count() == 0
    assert page.locator(sel("confirmation-reference")).count() == 0
    shot(page, "uncertain")
    page.unroute(re.compile(r".*/reservations$"))
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation-reference"))
    assert page.text_content(sel("confirmation-reference")) == store[0]["reference"]
    assert page.locator(sel("booking-uncertain")).count() == 0
    assert page.locator(sel("booking-error")).count() == 0
    assert len(api_list()) == 1 and page.keys[0] == page.keys[1]


def test_server_error_is_uncertain(seeded, page):
    log_in(page)
    open_form(page)
    page.route(re.compile(r".*/reservations$"),
               lambda r: r.fulfill(status=503, body="oops", content_type="text/plain")
               if r.request.method == "POST" else r.continue_())
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-uncertain"))
    assert page.locator(sel("booking-error")).count() == 0
    page.unroute(re.compile(r".*/reservations$"))
    page.route(re.compile(r".*/reservations$"),
               lambda r: r.fulfill(status=200, body="{not json", content_type="application/json")
               if r.request.method == "POST" else r.continue_())
    page.click(sel("booking-submit"))
    page.wait_for_timeout(800)
    assert page.locator(sel("booking-uncertain")).count() == 1, "unreadable 200 is uncertain"
    assert page.locator(sel("confirmation-reference")).count() == 0, "no manufactured success"
    page.unroute(re.compile(r".*/reservations$"))
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation-reference"))
    assert len(set(page.keys)) == 1


def test_4xx_after_uncertain(seeded, page):
    log_in(page)
    open_form(page)
    page.route(re.compile(r".*/reservations$"),
               lambda r: r.abort("timedout") if r.request.method == "POST" else r.continue_())
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-uncertain"))
    page.unroute(re.compile(r".*/reservations$"))
    login(BOB).post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_2",
                                            "starts_at_local": f"{day()}T19:00", "party_size": 2}, key_=key())
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-error"))
    wait_gone(page, "booking-uncertain")
    assert page.locator(sel("confirmation-reference")).count() == 0


# ---------------------------------------------------------------- lookup

def lookup(page, ref):
    page.goto("/lookup")
    page.fill(sel("lookup-reference-input"), ref)
    page.click(sel("lookup-submit"))
    page.wait_for_selector(f"{sel('reservation-detail')}, {sel('reservation-error')}")


def test_lookup_matrix(page):
    past = day(lead=-2)
    seeded_res = [{"id": "res_past", "reference": "PAST01", "user_id": "u_ada", "restaurant_id": "r_anker",
                   "table_id": "t_2", "starts_at_local": f"{past}T19:00", "party_size": 2},
                  {"id": "res_bob", "reference": "BOBS01", "user_id": "u_bob", "restaurant_id": "r_anker",
                   "table_id": "t_3", "starts_at_local": f"{day()}T19:00", "party_size": 2}]
    reset(fixture(restaurants=[ui_restaurant()], reservations=seeded_res))
    mine = ok(login(ADA).post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_2",
                                                      "starts_at_local": f"{day()}T19:00", "party_size": 2},
                              key_=key()), 201)["reference"]
    lookup(page, mine)  # signed out
    assert present(page, "reservation-error") and page.locator(sel("reservation-detail")).count() == 0
    log_in(page)
    lookup(page, "NOPE0000")
    assert present(page, "reservation-error")
    lookup(page, "BOBS01")
    assert present(page, "reservation-error"), "other people's bookings are not shown"
    lookup(page, "PAST01")
    assert page.text_content(sel("reservation-status")) == "confirmed"
    page.click(sel("reservation-cancel-button"))
    page.wait_for_selector(sel("reservation-error"))
    if page.locator(sel("reservation-status")).count():
        assert page.text_content(sel("reservation-status")) == "confirmed"
    lookup(page, mine)
    assert page.locator(sel("reservation-error")).count() == 0
    assert page.text_content(sel("reservation-status")) == "confirmed"
    assert "Garden" in page.text_content(sel("reservation-tables"))
    shot(page, "lookup-confirmed")
    page.click(sel("reservation-cancel-button"))
    page.wait_for_function("(document.querySelector(\"[data-testid='reservation-status']\")||{}).textContent"
                           " === 'cancelled'")
    assert page.locator(sel("reservation-cancel-button")).count() == 0
    shot(page, "lookup-cancelled")


# ---------------------------------------------------------------- upgrade

def upgrade_in_place():
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)


def test_retry_survives_upgrade(seeded, page):
    log_in(page)
    open_form(page, cell="slot-t_3+t_2-19:00", party=8)
    store = []
    lose_after_commit(page, store)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-uncertain"))
    page.unroute(re.compile(r".*/reservations$"))
    upgrade_in_place()
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation-reference"))
    assert page.text_content(sel("confirmation-reference")) == store[0]["reference"]
    assert len(api_list()) == 1


def test_signed_in_survives_upgrade(seeded, page):
    log_in(page)
    page.goto("/lookup")
    upgrade_in_place()
    page.goto("/")
    page.wait_for_selector(sel("current-user"))
    search(page, party=2, goto=False)
    page.click(sel("slot-t_1-19:00"))
    page.wait_for_selector(sel("booking-form"))
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation-reference"))


# ---------------------------------------------------------------- safety / quality

def test_no_html_injection(page):
    evil = '<img src=x onerror="window.__pwned=1">'
    r = ui_restaurant(name=f"Anker {evil}")
    r["tables"][1]["label"] = f"G{evil}"
    reset(fixture(users=[dict(ADA, display_name=f"Ada {evil}")], restaurants=[r]))
    log_in(page)
    assert evil in page.text_content(sel("current-user"))
    search(page, party=2, goto=True)
    page.click(sel("slot-t_2-19:00"))
    page.wait_for_selector(sel("booking-form"))
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation"))
    page.wait_for_timeout(300)
    assert page.evaluate("() => window.__pwned") is None
    assert page.locator("[data-testid='confirmation'] img").count() == 0


def test_no_external_requests(seeded, page):
    urls = []
    page.on("request", lambda r: urls.append(r.url))
    log_in(page)
    open_form(page, cell="slot-t_1+t_2-19:00", party=5)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation"))
    for route in ("/signup", "/lookup"):
        page.goto(route)
        page.wait_for_load_state("networkidle")
    ext = [u for u in urls if not (u.startswith(BASE) or u.startswith("data:") or u.startswith("blob:"))]
    assert not ext, ext


@pytest.mark.parametrize("width", [375, 1280])
def test_no_horizontal_scroll(seeded, browser, width):
    ctx = browser.new_context(base_url=BASE, viewport={"width": width, "height": 800})
    p = ctx.new_page()
    try:
        log_in(p)
        for route in ("/", "/signup", "/login", "/lookup"):
            p.goto(route)
            p.wait_for_load_state("networkidle")
            sw = p.evaluate("() => document.documentElement.scrollWidth")
            assert sw <= width, (route, sw)
            shot(p, f"{route.strip('/') or 'search'}-{width}")
        search(p, party=5, goto=True)
        p.click(sel("slot-t_1+t_2-19:00"))
        p.wait_for_selector(sel("booking-form"))
        p.click(sel("booking-submit"))
        p.wait_for_selector(sel("confirmation"))
        sw = p.evaluate("() => document.documentElement.scrollWidth")
        shot(p, f"grid-confirmation-{width}")
        assert sw <= width, ("grid", sw)
    finally:
        ctx.close()


def test_inputs_labelled_and_focus_visible(seeded, page):
    log_in(page)
    missing = []
    for route in ("/", "/signup", "/login", "/lookup"):
        page.goto(route)
        page.wait_for_load_state("networkidle")
        missing += page.evaluate("""(route) => [...document.querySelectorAll('input, select')]
            .filter(e => e.type !== 'hidden' && e.offsetParent !== null)
            .filter(e => !((e.labels && [...e.labels].some(l => l.innerText.trim())) ||
                           e.getAttribute('aria-labelledby')))
            .map(e => route + ' ' + (e.dataset.testid || e.name || e.id))""", route)
    assert not missing, f"inputs without a visible label: {missing}"
    page.goto("/")
    page.wait_for_load_state("networkidle")
    before = page.evaluate("""() => { const e = document.querySelector("[data-testid='search-button']");
        const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.boxShadow].join('|'); }""")
    page.focus(sel("date-input"))
    for _ in range(12):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.dataset.testid") == "search-button":
            break
    assert page.evaluate("() => document.activeElement.dataset.testid") == "search-button"
    after = page.evaluate("""() => { const s = getComputedStyle(document.activeElement);
        return [s.outlineStyle, s.outlineWidth, s.boxShadow].join('|'); }""")
    assert after != before and not after.startswith("none|0px|none"), (before, after)
