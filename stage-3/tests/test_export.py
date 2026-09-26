"""WO 1.5: export and import."""

import copy
import unittest

from support import ServiceTest


class ExportTest(ServiceTest):
    def setUp(self):
        self.reset()
        self.ada = self.login()

    def export(self):
        status, body, _ = self.client.get("/_test/export")
        self.assertEqual(status, 200)
        return body

    def test_round_trip(self):
        first = self.book(self.ada, key="k")[1]
        self.assertError(self.book(self.ada, key="failed", party_size=9), 422,
                         "party_exceeds_capacity")
        moved = self.client.post("/reservation-moves",
                                 {"moves": [{"reference": first["reference"]}]},
                                 token=self.ada, key="m")[1]
        snapshot = self.export()
        self.assertEqual((snapshot["track"], snapshot["format_version"]), ("tablekeeper", 1))
        self.assertIsInstance(snapshot["state"], dict)

        self.reset({})
        self.assertEqual(self.client.post("/_test/import", snapshot)[0], 204)
        self.assertEqual(self.client.post("/_test/import", snapshot)[0], 204)

        self.login()
        self.assertEqual(self.client.get(f"/reservations/{first['reference']}", token=self.ada)[1],
                         first)
        self.assertEqual(self.book(self.ada, key="k")[:2], (200, first))
        self.assertEqual(self.client.post("/reservation-moves",
                                          {"moves": [{"reference": first["reference"]}]},
                                          token=self.ada, key="m")[:2], (200, moved))
        self.assertError(self.book(self.ada, key="k", party_size=2), 409, "idempotency_key_reuse")
        self.assertEqual(self.book(self.ada, key="failed", table_id="t_3", party_size=6)[0], 201)
        reservations = self.client.get("/reservations", token=self.ada)[1]["reservations"]
        self.assertEqual(len(reservations), 2)
        self.assertEqual(self.export()["state"]["restaurants"], snapshot["state"]["restaurants"])

    def test_import_accepts_every_fixture_it_exported(self):
        user = {"id": "u_e", "email": "e@x.io", "password": "correct horse", "display_name": ""}
        f = {"users": [user], "restaurants": [], "reservations": []}
        self.reset(f)
        snapshot = self.export()
        self.assertEqual(self.client.post("/_test/import", snapshot)[0], 204)
        self.assertEqual(self.export(), snapshot)
        self.login("e@x.io", "correct horse")

    def test_export_is_a_snapshot(self):
        snapshot = self.export()
        before = copy.deepcopy(snapshot)
        self.book(self.ada)
        self.assertEqual(snapshot, before)

    def test_invalid_imports_leave_state(self):
        self.book(self.ada)
        good = self.export()
        bad_state = copy.deepcopy(good)
        bad_state["state"]["reservations"][0]["table_id"] = "nope"
        for body in ({}, [], dict(good, track="other"), dict(good, format_version=2),
                     dict(good, state="x"), {"track": "tablekeeper", "format_version": 1},
                     bad_state):
            self.assertError(self.client.post("/_test/import", body), 422, "validation_failed")
        self.assertError(self.client.post("/_test/import", raw=b"{"), 400, "malformed_request")
        self.assertEqual(self.export(), good)

    def test_reset_clears_imported_state(self):
        self.book(self.ada)
        snapshot = self.export()
        self.client.post("/_test/import", snapshot)
        self.reset()
        self.assertEqual(self.export()["state"]["reservations"], [])
        self.assertError(self.client.get("/reservations", token=self.ada), 401, "unauthenticated")


if __name__ == "__main__":
    unittest.main()
