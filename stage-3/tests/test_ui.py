"""WO 2.3: browser behaviour, driven through data-testid attributes.

Needs the `playwright` package with Chromium; skipped when it is not installed."""

import json
import unittest

from support import FIXTURE, FUTURE, ServiceTest, fixture
from test_combined import COMBO

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

WEEKDAY_COMBO = dict(COMBO, opening_hours=[
    {"weekday": d, "opens": "18:00", "closes": "22:00"}
    for d in ("mon", "tue", "wed", "thu", "fri", "sat")])


def T(testid):
    return f'[data-testid="{testid}"]'


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class UiTest(ServiceTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.base = f"http://127.0.0.1:{cls.client.port}"

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        self.reset(fixture(restaurants=[WEEKDAY_COMBO] + FIXTURE["restaurants"]))
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.page.set_default_timeout(8000)

    def tearDown(self):
        self.page.close()

    def login(self, email="ada@example.com", password="correct horse"):
        p = self.page
        p.goto(self.base + "/login")
        p.fill(T("login-email"), email)
        p.fill(T("login-password"), password)
        p.click(T("login-submit"))
        p.wait_for_selector(T("current-user"))

    def search(self, restaurant="r_combo", date=FUTURE, party=2):
        p = self.page
        if not p.url.rstrip("/").endswith(str(self.client.port)):
            p.goto(self.base + "/")
        p.select_option(T("restaurant-select"), restaurant)
        p.fill(T("date-input"), date)
        p.fill(T("party-size-input"), str(party))
        p.click(T("search-button"))

    def reservations(self, token_page=None):
        token = json.loads(self.page.evaluate("localStorage.getItem('tablekeeper.session')"))["token"]
        return self.client.get("/reservations", token=token)[1]["reservations"]

    def test_screens_are_html_and_navigable(self):
        for path in ("/", "/signup", "/login", "/lookup"):
            resp = self.page.goto(self.base + path)
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers["content-type"].startswith("text/html"))
        self.page.goto(self.base + "/signup")
        self.page.fill(T("signup-email"), "cy@example.com")
        self.page.fill(T("signup-password"), "longenough")
        self.page.fill(T("signup-display-name"), "<b>Cy</b>")
        self.page.click(T("signup-submit"))
        self.page.wait_for_selector(T("current-user"))
        self.assertIn("<b>Cy</b>", self.page.inner_text(T("current-user")))
        for path in ("/lookup", "/login", "/"):
            self.page.goto(self.base + path)
            self.page.wait_for_selector(T("current-user"))
        self.page.click(T("logout-button"))
        self.assertEqual(self.page.locator(T("current-user")).count(), 0)

    def test_auth_errors(self):
        p = self.page
        p.goto(self.base + "/login")
        self.assertEqual(p.locator(T("auth-error")).count(), 0)
        p.fill(T("login-email"), "ada@example.com")
        p.fill(T("login-password"), "wrong password")
        p.click(T("login-submit"))
        p.wait_for_selector(T("auth-error"))
        self.assertEqual(p.locator(T("current-user")).count(), 0)

    def test_grid_cells_and_signed_out_click(self):
        p = self.page
        self.search(party=5)
        p.wait_for_selector(T("availability-grid"))
        self.assertEqual(p.get_attribute(T("slot-t_1-19:00"), "data-available"), "false")
        self.assertEqual(p.get_attribute(T("slot-t_2+t_1-19:00"), "data-available"), "true")
        self.assertEqual(p.get_attribute(T("slot-t_2+t_3-19:00"), "data-available"), "true")
        self.search(party=7)
        p.wait_for_selector(T("slot-t_2+t_3-19:00"))
        self.assertEqual(p.locator(T("slot-t_2+t_1-19:00")).count(), 0)
        p.click(T("slot-t_1-19:00"))  # unavailable: nothing happens
        self.assertEqual(p.locator(T("booking-form")).count(), 0)
        self.assertEqual(p.locator(T("auth-error")).count(), 0)
        p.click(T("slot-t_2+t_3-19:00"))
        p.wait_for_selector(T("auth-error"))
        self.assertEqual(p.locator(T("availability-grid")).count(), 1)
        self.search(date="2030-09-29")  # a Sunday: closed
        p.wait_for_selector(T("no-slots"))
        self.assertEqual(p.locator(T("availability-grid")).count(), 0)

    def test_book_resubmit_and_change(self):
        p = self.page
        self.login()
        self.search(party=6)
        p.click(T("slot-t_2+t_1-19:00"))
        summary = p.inner_text(T("booking-summary"))
        self.assertIn("Table 1", summary)
        self.assertIn("Table 2", summary)
        self.assertIn("19:00", summary)
        self.assertEqual(p.input_value(T("booking-party-size")), "6")
        p.click(T("booking-submit"))
        p.wait_for_selector(T("confirmation-reference"))
        ref = p.inner_text(T("confirmation-reference"))
        self.assertRegex(ref, r"^[A-Z0-9]{6,12}$")
        details = p.inner_text(T("confirmation-details"))
        for text in ("Tafelrunde", "Table 1", "Table 2", "19:00", "Thu 26 Sep 2030"):
            self.assertIn(text, details)
        self.assertIn("Table 1", p.inner_text(T("confirmation-tables")))
        # Unchanged resubmission: the same reference, no error, no second booking.
        p.click(T("booking-submit"))
        p.wait_for_timeout(300)
        self.assertEqual(p.inner_text(T("confirmation-reference")), ref)
        self.assertEqual(p.locator(T("booking-error")).count(), 0)
        self.assertEqual(len(self.reservations()), 1)
        # A changed field is a new booking request; this table set is now taken.
        p.fill(T("booking-party-size"), "5")
        p.click(T("booking-submit"))
        p.wait_for_selector(T("booking-error"))
        self.assertEqual(p.locator(T("confirmation")).count(), 0)
        self.assertEqual(len(self.reservations()), 1)
        # Lookup shows the combined booking and cancels it.
        p.goto(self.base + "/lookup")
        p.fill(T("lookup-reference-input"), ref)
        p.click(T("lookup-submit"))
        p.wait_for_selector(T("reservation-detail"))
        self.assertEqual(p.inner_text(T("reservation-status")), "confirmed")
        self.assertIn("Table 2", p.inner_text(T("reservation-tables")))
        p.click(T("reservation-cancel-button"))
        p.wait_for_selector(f'{T("reservation-status")}:text-is("cancelled")')
        self.assertEqual(p.locator(T("reservation-cancel-button")).count(), 0)
        p.fill(T("lookup-reference-input"), "NOPE0000")
        p.click(T("lookup-submit"))
        p.wait_for_selector(T("reservation-error"))

    def test_conflict_refreshes_and_keeps_form(self):
        p = self.page
        self.login()
        self.search(party=2)
        p.click(T("slot-t_1-19:00"))
        p.fill(T("booking-party-size"), "1")
        bob = self.client.post("/auth/login", {"email": "bob@example.com",
                                               "password": "battery staple"})[1]["token"]
        self.book(bob, restaurant_id="r_combo", table_id="t_1", party_size=2)
        p.click(T("booking-submit"))
        p.wait_for_selector(T("booking-error"))
        p.wait_for_selector(f'{T("slot-t_1-19:00")}[data-available="false"]')
        self.assertEqual(p.input_value(T("booking-party-size")), "1")
        self.assertEqual(p.locator(T("booking-form")).count(), 1)
        self.assertEqual(p.locator(T("confirmation")).count(), 0)

    def test_lost_response_retries_with_same_key(self):
        p = self.page
        self.login()
        self.search(party=2)
        p.click(T("slot-t_3-19:00"))
        keys = []

        def lose(route):
            keys.append(route.request.headers.get("idempotency-key"))
            route.fetch()  # the booking commits on the server
            route.abort()  # but the browser never sees the response
        p.route("**/reservations", lose)
        p.click(T("booking-submit"))
        p.wait_for_selector(T("booking-uncertain"))
        self.assertTrue(p.inner_text(T("booking-uncertain")).strip())
        self.assertEqual(p.locator(T("booking-error")).count(), 0)
        self.assertEqual(p.locator(T("confirmation")).count(), 0)
        committed = self.reservations()
        self.assertEqual(len(committed), 1)
        p.unroute("**/reservations")

        # An export/import upgrade between requests keeps the pending retry working.
        snapshot = self.client.get("/_test/export")[1]
        self.reset({})
        self.assertEqual(self.client.post("/_test/import", snapshot)[0], 204)

        seen = []
        p.route("**/reservations", lambda route: (seen.append(
            route.request.headers.get("idempotency-key")), route.continue_()))
        p.click(T("booking-submit"))
        p.wait_for_selector(T("confirmation-reference"))
        self.assertEqual(seen, keys)
        self.assertEqual(p.inner_text(T("confirmation-reference")), committed[0]["reference"])
        self.assertEqual(p.locator(T("booking-uncertain")).count(), 0)
        self.assertEqual(len(self.reservations()), 1)

    def test_late_search_response_is_ignored(self):
        p = self.page
        held = []

        def hold_first(route):
            if not held and "restaurant_id=r_anker" in route.request.url:
                held.append(route)
            else:
                route.continue_()
        p.route("**/availability?*", hold_first)
        p.goto(self.base + "/")
        self.search(restaurant="r_anker", party=2)
        p.wait_for_timeout(200)
        self.search(restaurant="r_combo", party=5)
        p.wait_for_selector(T("slot-t_2+t_1-19:00"))
        held[0].continue_()
        p.wait_for_timeout(500)
        self.assertEqual(p.locator(T("slot-t_2+t_1-19:00")).count(), 1)
        self.assertIn("Tafelrunde", p.inner_text(T("availability-grid")) + p.inner_text("main"))
        self.assertNotIn("Zum Anker", p.inner_text("main .results"))

    def test_grid_uses_the_policy_capacities_of_the_date(self):
        combo = dict(WEEKDAY_COMBO, manager_user_ids=["u_bob"])
        self.reset(fixture(restaurants=[combo] + FIXTURE["restaurants"]))
        bob = self.client.post("/auth/login", {"email": "bob@example.com",
                                               "password": "battery staple"})[1]["token"]
        policy = {"effective_from": FUTURE, "slot_minutes": 30, "reservation_duration_minutes": 90,
                  "cancellation_cutoff_minutes": 60,
                  "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "22:00"}],
                  "capacities": {"t_1": 2, "t_2": 2, "t_3": 4}}
        status, _, _ = self.client.post("/restaurants/r_combo/policies", policy, token=bob, key="p")
        self.assertEqual(status, 201)
        p = self.page
        self.search(party=5)
        p.wait_for_selector(T("slot-t_2+t_3-19:00"))
        self.assertEqual(p.locator(T("slot-t_2+t_1-19:00")).count(), 0)
        self.search(date="2030-09-19", party=5)  # before the policy: fixture capacities
        p.wait_for_selector(T("slot-t_2+t_1-19:00"))

    def test_no_horizontal_scroll_on_small_screens(self):
        p = self.page
        p.set_viewport_size({"width": 375, "height": 800})
        self.login()
        for path in ("/", "/signup", "/login", "/lookup"):
            p.goto(self.base + path)
            p.wait_for_selector(T("current-user"))
            if path == "/":
                self.search(party=2)
                p.wait_for_selector(T("availability-grid"))
            self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 375, path)


if __name__ == "__main__":
    unittest.main()
